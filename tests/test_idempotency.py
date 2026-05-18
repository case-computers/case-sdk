"""P11 idempotency cache tests.

A WS hiccup can cause the server to re-send the same action with the
same step_id. Without an idempotency cache the client would double-click.
This test stands up a *tiny* custom WS server inside the test so we can
deterministically replay a step.

(The mock_server's procedure-driven flow won't produce duplicates, so
we don't reuse it here.)
"""

from __future__ import annotations

import asyncio
import threading

from websockets.asyncio.server import serve

from case_sdk.drivers.remote import RemoteDriver
from case_sdk.harness.executor import Harness
from case_sdk.protocol import (
    Envelope,
    action_msg,
    done_msg,
    error_msg,
)


def _replay_server_handler():
    """Build a handler that sends step_id=1 twice, then `done`."""

    async def handler(ws):
        # Auth check — match the real server so RemoteDriver's bearer
        # header doesn't cause an upgrade failure.
        # In websockets 16 the request headers live on ws.request.
        try:
            req_headers = ws.request.headers
        except AttributeError:
            req_headers = {}
        if req_headers.get("Authorization") != "Bearer dev-key":
            await ws.close(code=1008)
            return

        raw = await ws.recv()
        hello_env = Envelope.from_json(raw)
        session_id = hello_env.session_id

        # First send of step_id=1.
        await ws.send(
            action_msg(
                "open_app",
                {"name": "TextEdit"},
                session_id=session_id,
                step_id=1,
            ).to_json()
        )
        first_result = await ws.recv()
        first_env = Envelope.from_json(first_result)
        assert first_env.type == "result"
        assert first_env.step_id == 1

        # Replay step_id=1 — same envelope, same step_id. Client should
        # return cached observation without invoking the harness.
        await ws.send(
            action_msg(
                "open_app",
                {"name": "TextEdit"},
                session_id=session_id,
                step_id=1,
            ).to_json()
        )
        second_result = await ws.recv()
        second_env = Envelope.from_json(second_result)
        assert second_env.type == "result"
        assert second_env.step_id == 1

        # Finish the procedure so the client returns cleanly.
        await ws.send(
            done_msg(success=True, result={"replayed": True}, session_id=session_id).to_json()
        )

    return handler


class _TestServer:
    """Tiny self-contained WS server running on its own asyncio loop."""

    def __init__(self, handler):
        self.handler = handler
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        async def _serve():
            self._loop = asyncio.get_running_loop()
            self._server = await serve(self.handler, "127.0.0.1", 0)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()
            # Idle until stop is signalled.
            stop_future: asyncio.Future = asyncio.Future()

            def _check_stop():
                if self._stop.is_set():
                    stop_future.set_result(None)
                else:
                    self._loop.call_later(0.05, _check_stop)

            self._loop.call_soon(_check_stop)
            await stop_future
            self._server.close()
            await self._server.wait_closed()

        asyncio.run(_serve())

    def __enter__(self) -> "_TestServer":
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("test ws server never bound")
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def test_replay_returns_cached_observation_without_re_executing_harness():
    h = Harness()
    invocations: list[dict] = []

    @h.register_action("open_app")
    def open_app(name: str) -> dict:
        invocations.append({"name": name})
        # Distinct returned dict so we can verify cache fidelity.
        return {"opened": name, "instance": len(invocations)}

    with _TestServer(_replay_server_handler()) as srv:
        driver = RemoteDriver(
            h,
            api_base=f"ws://127.0.0.1:{srv.port}",
            api_key="dev-key",
            max_reconnects=0,
        )
        result = driver.execute("any_name", {})

    # Harness called exactly once even though server sent step_id=1 twice.
    assert len(invocations) == 1
    assert invocations[0] == {"name": "TextEdit"}
    # `result` is the procedure return value the mock server emitted.
    assert result == {"replayed": True}


def _replay_with_observation_check_handler():
    """Same as replay handler but verifies the *cached* observation is
    returned identically on the replay."""

    captured = {"first_obs": None, "second_obs": None}

    async def handler(ws):
        try:
            req_headers = ws.request.headers
        except AttributeError:
            req_headers = {}
        if req_headers.get("Authorization") != "Bearer dev-key":
            await ws.close(code=1008)
            return

        raw = await ws.recv()
        hello_env = Envelope.from_json(raw)
        session_id = hello_env.session_id

        await ws.send(
            action_msg(
                "open_app", {"name": "X"}, session_id=session_id, step_id=1
            ).to_json()
        )
        first_env = Envelope.from_json(await ws.recv())
        captured["first_obs"] = first_env.payload.get("observation")

        await ws.send(
            action_msg(
                "open_app", {"name": "X"}, session_id=session_id, step_id=1
            ).to_json()
        )
        second_env = Envelope.from_json(await ws.recv())
        captured["second_obs"] = second_env.payload.get("observation")

        await ws.send(
            done_msg(success=True, session_id=session_id).to_json()
        )

    return handler, captured


def test_replay_returns_identical_observation_payload():
    h = Harness()
    counter = {"n": 0}

    @h.register_action("open_app")
    def open_app(name: str) -> dict:
        counter["n"] += 1
        return {"opened": name, "harness_call_number": counter["n"]}

    handler, captured = _replay_with_observation_check_handler()
    with _TestServer(handler) as srv:
        driver = RemoteDriver(
            h,
            api_base=f"ws://127.0.0.1:{srv.port}",
            api_key="dev-key",
            max_reconnects=0,
        )
        driver.execute("any_name", {})

    # The cached observation on the replay must be byte-identical to the
    # one returned from the live call — otherwise the cache is shadowing
    # different data and that's a real bug.
    assert captured["first_obs"] == captured["second_obs"]
    assert counter["n"] == 1
