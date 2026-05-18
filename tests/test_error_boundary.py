"""P9 error boundary tests.

Covers both LocalDriver and RemoteDriver because parity is a hard
constraint: the same procedure code should behave identically in either
mode when a harness call fails.
"""

from __future__ import annotations

import time

import pytest

from case_sdk import Action
from case_sdk.drivers.local import LocalDriver
from case_sdk.drivers.remote import RemoteDriver
from case_sdk.harness.executor import Harness
from case_sdk.protocol import ClientError, ProcedureError

from tests._mock_server_fixture import fake_harness_recording, mock_server


# ---- LocalDriver ---------------------------------------------------------


def test_local_driver_harness_exception_caught_by_procedure():
    """Procedure recovers from a transient client failure via try/except."""

    fail_once = {"count": 0}

    h = Harness()

    @h.register_action("flaky")
    def flaky(**_kwargs):
        fail_once["count"] += 1
        if fail_once["count"] == 1:
            raise RuntimeError("hardware twitch")
        return {"ok": True}

    driver = LocalDriver(h)

    def proc(params):
        for attempt in range(3):
            try:
                yield Action("flaky")
                return {"success": True, "attempts": attempt + 1}
            except ClientError:
                continue
        raise RuntimeError("exhausted retries")

    assert driver.execute(proc, {}) == {"success": True, "attempts": 2}
    assert fail_once["count"] == 2


def test_local_driver_unhandled_failure_becomes_procedure_error():
    h = Harness()

    @h.register_action("explode")
    def explode(**_kwargs):
        raise RuntimeError("kaboom")

    driver = LocalDriver(h)

    def proc(params):
        yield Action("explode")
        return {"success": True}

    with pytest.raises(ProcedureError) as exc:
        driver.execute(proc, {})
    assert "kaboom" in str(exc.value)


def test_local_driver_per_step_timeout_fires():
    h = Harness()

    @h.register_action("slow")
    def slow(**_kwargs):
        time.sleep(2.0)
        return {}

    # 0.2s budget so the test finishes fast.
    driver = LocalDriver(h, step_timeout=0.2)

    captured: list[ClientError] = []

    def proc(params):
        try:
            yield Action("slow")
        except ClientError as e:
            captured.append(e)
            return {"success": False, "code": e.code}
        return {"success": True}

    result = driver.execute(proc, {})
    assert result == {"success": False, "code": "E_TIMEOUT"}
    assert len(captured) == 1
    assert captured[0].code == "E_TIMEOUT"


def test_local_driver_procedure_raises_unhandled_exception():
    """Anything the procedure raises that isn't caught → ProcedureError."""
    h = Harness()

    @h.register_action("noop")
    def noop(**_):
        return {}

    driver = LocalDriver(h)

    def proc(params):
        yield Action("noop")
        raise ValueError("procedure decided to give up")

    with pytest.raises(ProcedureError) as exc:
        driver.execute(proc, {})
    assert "ValueError" in str(exc.value)


# ---- RemoteDriver --------------------------------------------------------


def test_remote_driver_server_error_envelope_raises_procedure_error():
    """Server reports an error frame → client closes WS, raises ProcedureError."""
    with mock_server(error_on_step=2) as srv:
        harness, _ = fake_harness_recording()
        driver = RemoteDriver(
            harness,
            api_base=str(srv["url"]),
            api_key="dev-key",
            max_reconnects=0,
        )
        with pytest.raises(ProcedureError) as exc:
            driver.execute("save_textedit", {})
    assert "step 2" in str(exc.value) or "E_SERVER_FAIL" in exc.value.code


def test_remote_driver_per_step_timeout_fires():
    """Server hangs after step 2; client's step_timeout should fire."""
    with mock_server(hang_on_step=2) as srv:
        harness, _ = fake_harness_recording()
        driver = RemoteDriver(
            harness,
            api_base=str(srv["url"]),
            api_key="dev-key",
            step_timeout=0.5,
            max_reconnects=0,
        )
        with pytest.raises(ClientError) as exc:
            driver.execute("save_textedit", {})
    assert exc.value.code == "E_TIMEOUT"


def test_remote_driver_harness_failure_surfaces_to_server():
    """When the client's harness raises, the server sees an ok=False result.

    The mock server's procedure does not have a try/except so the
    server-side procedure aborts and emits an error envelope, which we
    expect to surface as ProcedureError on the client.
    """
    h = Harness()

    @h.register_action("open_app")
    def open_app(**_):
        raise RuntimeError("simulated harness explosion")

    # Fill in the rest so the procedure could keep running if we somehow
    # didn't blow up first.
    @h.register_action("wait")
    def wait(**_):
        return {}

    @h.register_action("type")
    def type_(**_):
        return {}

    @h.register_action("hotkey")
    def hotkey(**_):
        return {}

    with mock_server() as srv:
        driver = RemoteDriver(
            h,
            api_base=str(srv["url"]),
            api_key="dev-key",
            max_reconnects=0,
        )
        with pytest.raises(ProcedureError):
            driver.execute("save_textedit", {})
