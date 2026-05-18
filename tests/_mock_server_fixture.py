"""Shared subprocess-based fixture for the FastAPI mock server.

Tests spin up the real `tests/mock_server.py` in a subprocess so the WS
client code is exercised against actual uvicorn — not an in-process
ASGI hack. The fixture polls the /healthz endpoint until it returns
200, then yields connection info. Cleanup is best-effort terminate +
short wait.

Ports are chosen from a per-test counter starting in the high range so
parallel runs (pytest-xdist someday) don't collide with each other or
with an already-running dev server on 8000.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
MOCK_SERVER = REPO_ROOT / "tests" / "mock_server.py"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _free_port() -> int:
    """Bind to port 0, read back the kernel-assigned port, then release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(port: int, deadline: float) -> None:
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/healthz", timeout=0.5
            ) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
        time.sleep(0.05)
    raise RuntimeError(
        f"mock_server on port {port} never became ready; last_err={last_err!r}"
    )


@contextmanager
def mock_server(
    *,
    api_key: str = "dev-key",
    ping_interval: float | None = None,
    max_missed_pongs: int | None = None,
    error_on_step: int | None = None,
    hang_on_step: int | None = None,
    extra_args: list[str] | None = None,
) -> Iterator[dict[str, object]]:
    """Yield {url, port, api_key, proc} for a freshly-started mock server."""
    port = _free_port()
    cmd: list[str] = [
        str(PYTHON),
        str(MOCK_SERVER),
        "--port",
        str(port),
        "--api-key",
        api_key,
    ]
    if ping_interval is not None:
        cmd += ["--ping-interval", str(ping_interval)]
    if max_missed_pongs is not None:
        cmd += ["--max-missed-pongs", str(max_missed_pongs)]
    if error_on_step is not None:
        cmd += ["--error-on-step", str(error_on_step)]
    if hang_on_step is not None:
        cmd += ["--hang-on-step", str(hang_on_step)]
    if extra_args:
        cmd += extra_args

    env = os.environ.copy()
    # Quiet uvicorn so the test output isn't flooded.
    env.setdefault("PYTHONUNBUFFERED", "1")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        _wait_ready(port, deadline=time.time() + 5.0)
        yield {
            "url": f"ws://127.0.0.1:{port}",
            "port": port,
            "api_key": api_key,
            "proc": proc,
        }
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def fake_harness_recording() -> tuple:
    """Build a harness whose actions just record their call args.

    Used by tests that want to verify the dispatch sequence without
    actually opening TextEdit / typing into the real OS.
    """
    from case_sdk.harness.executor import Harness

    h = Harness()
    calls: list[tuple] = []

    @h.register_action("open_app")
    def open_app(name: str) -> dict:
        calls.append(("open_app", {"name": name}))
        return {}

    @h.register_action("wait")
    def wait(ms: int) -> dict:
        calls.append(("wait", {"ms": ms}))
        return {}

    @h.register_action("type")
    def type_(text: str) -> dict:
        calls.append(("type", {"text": text}))
        return {}

    @h.register_action("hotkey")
    def hotkey(combo: list) -> dict:
        calls.append(("hotkey", {"combo": combo}))
        return {}

    return h, calls
