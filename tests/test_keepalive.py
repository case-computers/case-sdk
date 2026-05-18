"""P10 keepalive + reconnect tests.

Real-time tests by design: the server's keepalive task uses asyncio.sleep
deadlines, so we can't usefully mock time without rewriting the server.
We pick short intervals (0.3s ping, 1 missed pong) so the whole suite
finishes in a few seconds.

Test strategy: register a *slow* harness primitive on the client. While
that primitive is running, the client's event loop is busy in
asyncio.to_thread waiting on it — it can't respond to pings. The server
times out the pong, kills the session, and the client reconnects with a
fresh session_id. On the second attempt we let the harness behave
normally so the procedure completes.
"""

from __future__ import annotations

import threading
import time
import urllib.request

from case_sdk.drivers.remote import RemoteDriver
from case_sdk.harness.executor import Harness
from tests._mock_server_fixture import mock_server


def _server_session_count(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz") as resp:
        import json

        return json.loads(resp.read())["sessions"]


def _make_slow_first_attempt_harness(delay_first_call: float) -> tuple[Harness, dict]:
    """First open_app blocks long enough to miss a ping; subsequent calls return."""
    state = {"open_app_calls": 0, "type_calls": 0}
    lock = threading.Lock()

    h = Harness()

    @h.register_action("open_app")
    def open_app(name: str) -> dict:
        with lock:
            state["open_app_calls"] += 1
            n = state["open_app_calls"]
        if n == 1:
            # Sleep long enough that we miss enough pings to be killed.
            time.sleep(delay_first_call)
        return {}

    @h.register_action("wait")
    def wait(ms: int) -> dict:
        return {}

    @h.register_action("type")
    def type_(text: str) -> dict:
        with lock:
            state["type_calls"] += 1
        return {}

    @h.register_action("hotkey")
    def hotkey(combo: list) -> dict:
        return {}

    return h, state


def test_server_kills_unresponsive_session_and_driver_reconnects():
    """Slow first attempt → server kills WS → driver reconnects → session #2 completes."""
    # Server config: 0.3s ping interval, kill after 1 missed pong. So the
    # client has roughly 0.6s total to respond to the first ping. We
    # sleep 2s on first open_app to guarantee a kill.
    with mock_server(
        ping_interval=0.3,
        max_missed_pongs=1,
    ) as srv:
        h, state = _make_slow_first_attempt_harness(delay_first_call=2.0)
        driver = RemoteDriver(
            h,
            api_base=str(srv["url"]),
            api_key="dev-key",
            step_timeout=5.0,
            max_reconnects=5,
            reconnect_backoff=(0.05,),
        )

        result = driver.execute("save_textedit", {})
        sessions = _server_session_count(int(srv["port"]))

    # Procedure completed eventually.
    assert result.get("success") is True
    # Second attempt's type call ran (first attempt died mid-flight).
    assert state["type_calls"] >= 1
    # Each fresh attempt = fresh session. We expect at least two.
    assert sessions >= 2


def test_normal_session_responds_to_pings_and_finishes():
    """Sanity: fast harness + tight ping interval still completes."""
    from tests._mock_server_fixture import fake_harness_recording

    with mock_server(ping_interval=0.5, max_missed_pongs=2) as srv:
        h, calls = fake_harness_recording()
        driver = RemoteDriver(
            h,
            api_base=str(srv["url"]),
            api_key="dev-key",
            max_reconnects=0,
        )
        result = driver.execute("save_textedit", {})

    assert result.get("success") is True
    assert len(calls) == 4
