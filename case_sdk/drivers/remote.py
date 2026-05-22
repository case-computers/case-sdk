"""Stream a remote procedure via WebSocket.

The local harness executes one step at a time; the procedure body never
crosses the wire. See ``case_sdk/drivers/local.py`` for the in-process
equivalent.
"""

from __future__ import annotations

import asyncio
import os
import random
import ssl
from typing import Any, Callable

import certifi
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from case_sdk.drivers.base import Driver
from case_sdk.harness.executor import Harness
from case_sdk.protocol import (
    ClientError,
    Envelope,
    ProcedureError,
    hello,
    pong_msg,
    result_msg,
)
from case_sdk.verify import VerifyContext
from case_sdk.verify.runner import VerifyError, run_verify

# Defaults match the plan: 30s per-step deadline, 5 attempts at backoff
# 1/2/4/8/30s. Constructor kwargs override; env vars override those for
# end-to-end tests that drive a long pipeline.
DEFAULT_STEP_TIMEOUT = float(os.environ.get("CASE_SDK_STEP_TIMEOUT", "30"))
DEFAULT_MAX_RECONNECTS = int(os.environ.get("CASE_SDK_MAX_RECONNECTS", "5"))
RECONNECT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 30.0)


def _ssl_context_for(url: str) -> ssl.SSLContext | None:
    """SSLContext rooted at certifi's CA bundle for wss:// URLs.

    macOS Python installers from python.org and several pyenv builds ship
    without a wired-up trust store, so the stdlib default ssl context can
    fail to verify Cloud Run / Let's Encrypt chains. Using certifi removes
    that dependency on the user's environment.

    Returns None for ws:// (no TLS) so local dev keeps working unchanged.
    """
    if not url.startswith("wss://"):
        return None
    return ssl.create_default_context(cafile=certifi.where())


class RemoteDriver(Driver):
    """Drives a procedure that lives on a remote server.

    Args:
        harness: local action/sensor implementations to dispatch into.
        api_base: the WS base URL (path `/ws` is appended). e.g.
            "ws://localhost:8000". Trailing /ws is allowed.
        api_key: bearer token sent on the WS upgrade. Empty string means
            no Authorization header which will be rejected by the server.
        step_timeout: max seconds to wait for a result-message turnaround
            on a single step before raising ClientError("E_TIMEOUT").
        max_reconnects: how many times to retry after a transport drop.
            0 disables reconnect entirely (good for tests).
    """

    def __init__(
        self,
        harness: Harness,
        api_base: str = "wss://api.daemonlabs.run",
        api_key: str = "",
        *,
        step_timeout: float = DEFAULT_STEP_TIMEOUT,
        max_reconnects: int = DEFAULT_MAX_RECONNECTS,
        reconnect_backoff: tuple[float, ...] = RECONNECT_BACKOFF,
    ) -> None:
        self.harness = harness
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.step_timeout = step_timeout
        self.max_reconnects = max_reconnects
        # Mutable per-instance backoff lets tests run fast (e.g. (0.05,)).
        # Production code should leave this at the default.
        self.reconnect_backoff = reconnect_backoff

    # ---- Driver ABC ----------------------------------------------------
    def execute(
        self,
        generator_fn: Callable[[dict[str, Any]], Any] | str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(generator_fn, str):
            raise TypeError(
                "RemoteDriver.execute expects a procedure name (str), got "
                f"{type(generator_fn).__name__}; pass procedure_name through Runtime.execute()"
            )
        return asyncio.run(self._run(generator_fn, params))

    # ---- async machinery ----------------------------------------------
    async def _run(
        self,
        procedure_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        attempt = 0

        while True:
            # Each attempt is a fresh session — old session_id is dead.
            # The server-side generator does not resume; the procedure
            # restarts from step 0.
            try:
                return await self._run_one_session(procedure_name, params)
            except _ReconnectableError as e:
                if attempt >= self.max_reconnects:
                    raise ProcedureError(
                        f"transport gave up after {attempt + 1} attempts: {e.cause}",
                        code="E_TRANSPORT",
                    ) from e.cause
                delay = self.reconnect_backoff[
                    min(attempt, len(self.reconnect_backoff) - 1)
                ]
                # Tiny jitter so two clients reconnecting at once don't
                # collide. Tests pin the deterministic part of the
                # backoff so this is safe.
                delay = delay + random.uniform(0, 0.05)
                await asyncio.sleep(delay)
                attempt += 1
                continue

    async def _run_one_session(
        self,
        procedure_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        url = self._ws_url()
        headers = {"Authorization": f"Bearer {self.api_key}"}

        # Idempotency cache is per-session. Fresh dict each attempt — see
        # the docstring on _run() above.
        step_cache: dict[int, dict[str, Any]] = {}

        # Per-session VerifyContext. Server-side verify steps (db_snapshot →
        # db_delta) need state to flow across the run (e.g. baseline lookup
        # by name). Mode is best-effort "soft" client-side; the server's
        # generator handles hard-mode by raising ClientError, which we
        # surface via an error envelope.
        verify_ctx = VerifyContext(mode="soft")

        # Disable websockets' built-in ping logic; this protocol uses
        # ping/pong envelopes at the application layer (so a network
        # box can't fake liveness by acking control frames).
        try:
            # Cap incoming frame size at 1 MB. Defensive against a malicious
            # or misbehaving server streaming oversized payloads that would
            # otherwise exhaust client memory. Matches the websockets default
            # but pinned so a future library default change doesn't relax it.
            ws_ctx = ws_connect(
                url,
                additional_headers=headers,
                ping_interval=None,
                open_timeout=10,
                max_size=1_000_000,
                ssl=_ssl_context_for(url),
            )
        except Exception as e:  # constructor itself rarely raises
            raise _ReconnectableError(e) from e

        try:
            async with ws_ctx as ws:
                hello_env = hello(procedure_name, params)
                await ws.send(hello_env.to_json())
                session_id = hello_env.session_id

                while True:
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=self.step_timeout
                        )
                    except asyncio.TimeoutError as e:
                        raise ClientError(
                            f"step timed out after {self.step_timeout}s",
                            code="E_TIMEOUT",
                        ) from e
                    except ConnectionClosed as e:
                        raise _ReconnectableError(e) from e

                    env = Envelope.from_json(raw)

                    if env.type == "done":
                        # Unwrap so callers see the same shape LocalDriver returns;
                        # fall back to {"success": ...} if the server omitted `result`.
                        result = env.payload.get("result")
                        if isinstance(result, dict) and result:
                            return result
                        return {"success": env.payload.get("success", True)}

                    if env.type == "ping":
                        await ws.send(pong_msg(session_id=env.session_id).to_json())
                        continue

                    if env.type == "error":
                        # Server says the procedure itself failed (or the
                        # server hit an internal limit). This is fatal —
                        # no reconnect, surface as ProcedureError.
                        code = env.payload.get("code", "E_SERVER")
                        msg = env.payload.get("message", "remote procedure error")
                        raise ProcedureError(msg, code=code)

                    if env.type in ("action", "sensor", "verify"):
                        cached = step_cache.get(env.step_id)
                        if cached is not None:
                            # Replay of a step we've already executed: return
                            # the cached observation so a WS hiccup that
                            # produced a duplicate frame never double-clicks.
                            await ws.send(
                                result_msg(
                                    ok=cached["ok"],
                                    observation=cached.get("observation"),
                                    error=cached.get("error"),
                                    session_id=session_id,
                                    step_id=env.step_id,
                                ).to_json()
                            )
                            continue

                        result_payload = await self._dispatch_step(env, verify_ctx)
                        step_cache[env.step_id] = result_payload
                        await ws.send(
                            result_msg(
                                ok=result_payload["ok"],
                                observation=result_payload.get("observation"),
                                error=result_payload.get("error"),
                                session_id=session_id,
                                step_id=env.step_id,
                            ).to_json()
                        )
                        continue

                    # Unknown envelope type — fail loud rather than
                    # silently looping.
                    raise ProcedureError(
                        f"unexpected envelope type {env.type!r} from server",
                        code="E_PROTOCOL",
                    )
        except ProcedureError:
            raise
        except ClientError:
            raise
        except InvalidStatus as e:
            # Auth rejection or other handshake failure. The server may
            # be wrong about us (transient) but the most common case is
            # a bad API key — don't retry.
            raise ProcedureError(
                f"server rejected handshake: {e}",
                code="E_AUTH",
            ) from e
        except (ConnectionClosed, WebSocketException, OSError) as e:
            raise _ReconnectableError(e) from e

    async def _dispatch_step(
        self, env: Envelope, verify_ctx: VerifyContext
    ) -> dict[str, Any]:
        """Run one harness / verifier call. Translates exceptions to result payloads.

        Returns a dict with at least an `ok` bool, plus either
        `observation` or `error`. Caller stuffs that into a result
        envelope.
        """
        name = env.payload.get("name", "")
        args = env.payload.get("args", {})

        async def _call() -> dict[str, Any]:
            # All primitives are sync (pyautogui calls, sqlite reads).
            # Run on a worker thread so they don't block the event loop.
            if env.type == "action":
                return await asyncio.to_thread(
                    self.harness.execute_action, name, args
                )
            if env.type == "sensor":
                return await asyncio.to_thread(
                    self.harness.execute_sensor, name, args
                )
            # env.type == "verify" — run the local verifier framework.
            # run_verify returns a dict like {"ok": True, ...} or {"ok": False,
            # "reason": "..."}. Pass it through unchanged; the server side
            # decides soft vs hard handling.
            return await asyncio.to_thread(_run_verify_safe, name, args, verify_ctx)

        try:
            observation = await asyncio.wait_for(_call(), timeout=self.step_timeout)
            return {"ok": True, "observation": observation or {}}
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": f"E_TIMEOUT: harness step exceeded {self.step_timeout}s",
            }
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _ws_url(self) -> str:
        base = self.api_base
        # Allow either "ws://host" or "ws://host/ws" to be passed in.
        if base.endswith("/ws"):
            return base
        return f"{base}/ws"


class _ReconnectableError(Exception):
    """Internal: transport-level failure that triggers a reconnect attempt."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _run_verify_safe(
    name: str, args: dict[str, Any], ctx: VerifyContext
) -> dict[str, Any]:
    """Wrap run_verify so any failure surfaces as ``{"ok": False, "reason": ...}``
    rather than crashing the dispatch — the server's generator then decides
    soft (log + continue) vs hard (raise ClientError) handling. Includes
    non-VerifyError exceptions (e.g. FileNotFoundError when DaVinci isn't
    installed or sqlite path doesn't exist on this Mac)."""
    try:
        return run_verify(name, args, ctx)
    except VerifyError as e:
        return {"ok": False, "reason": str(e)}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
