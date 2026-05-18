"""Minimal FastAPI+WS mock server for verifying RemoteDriver.

This is a *test-only* server. It hosts a single hard-coded procedure
(`save_textedit`) and walks its generator over a WebSocket, exactly as
case-api will do in production. The goal is to give RemoteDriver
something realistic to talk to without depending on the closed-source
case-api repo.

Run standalone:
    python tests/mock_server.py [--port 8000] [--ping-interval 20]
                                [--max-missed-pongs 2]
                                [--error-on-step N] [--hang-on-step N]

Auth:
    Header `Authorization: Bearer dev-key` is required on the WS upgrade.
    Anything else → close 1008 (policy violation).

Behaviour knobs:
    --ping-interval     seconds between server-initiated ping_msg frames
                        (small values are useful for testing P10 keepalive)
    --max-missed-pongs  number of unanswered pings before the server kills
                        the session (P10)
    --error-on-step     if set, server sends an error envelope after the
                        client's result_msg for that step_id (P9)
    --hang-on-step      if set, server stops responding after that step_id
                        (P9 client-side timeout test)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Generator

# Make the parent dir importable so `python tests/mock_server.py` works
# even when this file is run directly (no installed package).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402

from case_sdk.protocol import (  # noqa: E402
    Action,
    Envelope,
    action_msg,
    done_msg,
    error_msg,
    ping_msg,
)

# ---- Hard-coded procedures ---------------------------------------------
# Server-side procedures are generators of `Action` (or `Sensor`) values,
# identical in shape to the local procedure files Team 1 ships. The mock
# server only needs one for end-to-end verification: save_textedit.


def _save_textedit(params: dict[str, Any]) -> Generator[Action, dict, dict]:
    text = params.get("text", "hi from case-sdk")
    yield Action("open_app", name="TextEdit")
    yield Action("wait", ms=500)
    yield Action("type", text=text)
    yield Action("hotkey", combo=["cmd", "s"])
    return {"success": True}


PROCEDURES: dict[str, Callable[[dict[str, Any]], Generator]] = {
    "save_textedit": _save_textedit,
}


# ---- App factory --------------------------------------------------------
# Built as a factory so tests can spin up multiple instances with different
# keepalive settings without touching globals.


def build_app(
    *,
    api_key: str = "dev-key",
    ping_interval: float = 20.0,
    max_missed_pongs: int = 2,
    error_on_step: int | None = None,
    hang_on_step: int | None = None,
) -> FastAPI:
    app = FastAPI()

    # Session counter is global per-process so tests can verify reconnects
    # produce fresh sessions.
    app.state.session_counter = 0
    app.state.sessions: list[str] = []

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "sessions": app.state.session_counter}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        # Validate auth BEFORE accepting the upgrade — the spec says
        # missing / wrong Bearer => close 1008.
        auth = websocket.headers.get("authorization", "")
        if auth != f"Bearer {api_key}":
            # Per FastAPI/Starlette: close without accept rejects upgrade
            # with HTTP 403, which surfaces to the websockets client as a
            # ConnectionClosed / InvalidStatus.
            await websocket.close(code=1008)
            return

        await websocket.accept()
        app.state.session_counter += 1

        try:
            await _run_session(
                websocket,
                app=app,
                ping_interval=ping_interval,
                max_missed_pongs=max_missed_pongs,
                error_on_step=error_on_step,
                hang_on_step=hang_on_step,
            )
        except WebSocketDisconnect:
            return

    return app


# ---- Per-session driver -------------------------------------------------
# Owns the procedure generator, the step counter, and the keepalive loop.
# Anything more complex (multi-procedure, retry, etc.) lives in case-api,
# not here.


async def _run_session(
    websocket: WebSocket,
    *,
    app: FastAPI,
    ping_interval: float,
    max_missed_pongs: int,
    error_on_step: int | None,
    hang_on_step: int | None,
) -> None:
    # Pong tracking shared between the keepalive task and the main loop.
    pong_event = asyncio.Event()
    missed_pongs = 0
    keepalive_task: asyncio.Task | None = None
    session_killed = False

    async def keepalive() -> None:
        nonlocal missed_pongs, session_killed
        while True:
            await asyncio.sleep(ping_interval)
            pong_event.clear()
            try:
                await websocket.send_text(ping_msg().to_json())
            except Exception:
                return
            try:
                await asyncio.wait_for(pong_event.wait(), timeout=ping_interval)
                missed_pongs = 0
            except asyncio.TimeoutError:
                missed_pongs += 1
                if missed_pongs >= max_missed_pongs:
                    session_killed = True
                    try:
                        await websocket.close(code=1011)
                    except Exception:
                        pass
                    return

    # 1) Wait for hello — gives us the procedure name + params + session_id.
    hello_raw = await websocket.receive_text()
    hello_env = Envelope.from_json(hello_raw)
    if hello_env.type != "hello":
        await websocket.send_text(
            error_msg(
                "E_PROTOCOL",
                f"expected hello, got {hello_env.type}",
                session_id=hello_env.session_id,
            ).to_json()
        )
        await websocket.close(code=1002)
        return

    session_id = hello_env.session_id
    app.state.sessions.append(session_id)
    proc_name = hello_env.payload.get("procedure", "")
    params = hello_env.payload.get("params", {})

    procedure_factory = PROCEDURES.get(proc_name)
    if procedure_factory is None:
        await websocket.send_text(
            error_msg(
                "E_UNKNOWN_PROCEDURE",
                f"no procedure named {proc_name!r}",
                session_id=session_id,
            ).to_json()
        )
        await websocket.close(code=1002)
        return

    gen = procedure_factory(params)
    step_id = 0

    # 2) Start keepalive AFTER hello so handshake costs no time off the
    # ping deadline.
    keepalive_task = asyncio.create_task(keepalive())

    async def recv_filtering_pong() -> Envelope:
        """Receive next envelope, transparently absorbing pong frames."""
        while True:
            raw = await websocket.receive_text()
            env = Envelope.from_json(raw)
            if env.type == "pong":
                pong_event.set()
                continue
            return env

    try:
        # 3) Drive the procedure generator yield-by-yield.
        try:
            yielded = next(gen)
        except StopIteration as stop:
            await _send_done(websocket, session_id, stop.value)
            return

        observation: dict[str, Any] | None = None
        while True:
            if not isinstance(yielded, Action):
                # Sensors are out of scope for the mock — only Actions in
                # save_textedit. If we ever yield something else, fail loud.
                await websocket.send_text(
                    error_msg(
                        "E_PROTOCOL",
                        f"mock server only supports Action yields, got {type(yielded).__name__}",
                        session_id=session_id,
                    ).to_json()
                )
                return

            step_id += 1
            await websocket.send_text(
                action_msg(
                    yielded.name,
                    yielded.args,
                    session_id=session_id,
                    step_id=step_id,
                ).to_json()
            )

            # P9: hang on a specific step — useful for client timeout
            # tests. We literally stop reading, the keepalive will
            # eventually kill the session.
            if hang_on_step is not None and step_id == hang_on_step:
                await asyncio.sleep(3600)
                return

            result_env = await recv_filtering_pong()

            if result_env.type != "result":
                # Client misbehaved.
                await websocket.send_text(
                    error_msg(
                        "E_PROTOCOL",
                        f"expected result, got {result_env.type}",
                        session_id=session_id,
                    ).to_json()
                )
                return

            # P9: inject server-side error on a specific step.
            if error_on_step is not None and step_id == error_on_step:
                await websocket.send_text(
                    error_msg(
                        "E_SERVER_FAIL",
                        f"injected failure on step {step_id}",
                        session_id=session_id,
                        step_id=step_id,
                    ).to_json()
                )
                return

            observation = result_env.payload.get("observation", {})
            ok = result_env.payload.get("ok", True)
            if not ok:
                # Client reported its harness failed. For the mock, we
                # surface that back to the generator via gen.throw — this
                # mirrors what case-api will do.
                err = result_env.payload.get("error", "client harness failure")
                try:
                    yielded = gen.throw(Exception(err))
                    continue
                except StopIteration as stop:
                    await _send_done(
                        websocket, session_id, stop.value, success=False, reason=err
                    )
                    return
                except Exception as e:
                    await websocket.send_text(
                        error_msg(
                            "E_PROCEDURE",
                            str(e),
                            session_id=session_id,
                        ).to_json()
                    )
                    return

            try:
                yielded = gen.send(observation)
            except StopIteration as stop:
                await _send_done(websocket, session_id, stop.value)
                return
            except Exception as e:
                await websocket.send_text(
                    error_msg(
                        "E_PROCEDURE",
                        str(e),
                        session_id=session_id,
                    ).to_json()
                )
                return
    finally:
        if keepalive_task is not None:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except (asyncio.CancelledError, Exception):
                pass


async def _send_done(
    websocket: WebSocket,
    session_id: str,
    value: Any,
    success: bool = True,
    reason: str = "",
) -> None:
    if isinstance(value, dict):
        result_payload = value
    elif value is None:
        result_payload = {}
    else:
        result_payload = {"value": value}
    try:
        await websocket.send_text(
            done_msg(
                success=success,
                reason=reason,
                result=result_payload,
                session_id=session_id,
            ).to_json()
        )
    except Exception:
        pass


# ---- Entry point --------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--api-key", default="dev-key")
    parser.add_argument("--ping-interval", type=float, default=20.0)
    parser.add_argument("--max-missed-pongs", type=int, default=2)
    parser.add_argument("--error-on-step", type=int, default=None)
    parser.add_argument("--hang-on-step", type=int, default=None)
    args = parser.parse_args()

    import uvicorn  # local import keeps cold-start cheap for tests

    app = build_app(
        api_key=args.api_key,
        ping_interval=args.ping_interval,
        max_missed_pongs=args.max_missed_pongs,
        error_on_step=args.error_on_step,
        hang_on_step=args.hang_on_step,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
