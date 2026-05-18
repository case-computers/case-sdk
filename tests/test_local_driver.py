"""Tests for LocalDriver using a fake (non-GUI) harness."""

import pytest

from case_sdk import Action, Sensor
from case_sdk.drivers.local import LocalDriver
from case_sdk.harness.executor import Harness
from case_sdk.protocol import ProcedureError


def make_fake_harness():
    h = Harness()
    calls: list[tuple] = []

    @h.register_action("noop")
    def noop(**kwargs):
        calls.append(("action", "noop", kwargs))
        return {}

    @h.register_sensor("constant")
    def constant(value):
        calls.append(("sensor", "constant", {"value": value}))
        return {"value": value}

    return h, calls


def test_drives_two_actions_in_order():
    h, calls = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        yield Action("noop", x=1)
        yield Action("noop", x=2)
        return {"success": True, "ran": 2}

    result = driver.execute(proc, {})
    assert result == {"success": True, "ran": 2}
    assert calls == [
        ("action", "noop", {"x": 1}),
        ("action", "noop", {"x": 2}),
    ]


def test_sensor_result_is_sent_back_into_generator():
    h, _ = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        obs = yield Sensor("constant", value=42)
        return {"got": obs["value"]}

    assert driver.execute(proc, {}) == {"got": 42}


def test_procedure_can_consume_params():
    h, _ = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        yield Action("noop", echo=params["greeting"])
        return {"received": params["greeting"]}

    assert driver.execute(proc, {"greeting": "hi"}) == {"received": "hi"}


def test_default_return_when_procedure_returns_none():
    h, _ = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        yield Action("noop")
        # falls off the end with no `return`

    assert driver.execute(proc, {}) == {"success": True}


def test_procedure_with_no_yields_returns_default():
    h, _ = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        if False:
            yield Action("noop")  # generator with no actual yields

    assert driver.execute(proc, {}) == {"success": True}


def test_yielding_non_action_raises_typeerror():
    h, _ = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        yield "not an action"

    with pytest.raises(TypeError):
        driver.execute(proc, {})


def test_unknown_action_raises():
    """P9: harness exceptions become ClientError thrown into the generator.

    If the procedure does not catch them, the driver surfaces them as
    ProcedureError so the caller knows the procedure aborted.
    """
    h, _ = make_fake_harness()
    driver = LocalDriver(h)

    def proc(params):
        yield Action("does_not_exist")

    with pytest.raises(ProcedureError):
        driver.execute(proc, {})
