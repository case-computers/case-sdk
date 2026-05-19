"""Tests for JSON step-list procedures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from case_sdk import Action, Runtime, Sensor
from case_sdk.harness.executor import Harness
from case_sdk.steps import (
    StepsFormatError,
    load_steps_file,
    steps_to_procedure,
    substitute_params,
)


def test_sensor_save_as_injects_param():
    doc = {
        "steps": [
            {"sensor": "window_title", "save_as": "title"},
            {"action": "noop", "echo": "${title}"},
        ],
        "return": {"success": True},
    }
    h = Harness()
    seen: dict = {}

    @h.register_action("noop")
    def noop(echo: str = ""):
        seen["echo"] = echo
        return {}

    @h.register_sensor("window_title")
    def window_title():
        return {"value": "TextEdit"}

    rt = Runtime(harness=h)
    rt.local_driver.execute(steps_to_procedure(doc), {})
    assert seen.get("echo") == "TextEdit"


def test_substitute_params_in_strings():
    out = substitute_params("hello ${name}", {"name": "world"})
    assert out == "hello world"


def test_steps_to_procedure_yields_actions():
    doc = {
        "steps": [
            {"action": "wait", "ms": 10},
            {"sensor": "window_title"},
        ],
        "return": {"success": True},
    }
    gen = steps_to_procedure(doc)({})
    assert isinstance(next(gen), Action)
    assert isinstance(gen.send({}), Sensor)
    with pytest.raises(StopIteration) as exc_info:
        gen.send({"value": "x"})
    ret = exc_info.value.value
    assert ret["success"] is True
    assert ret.get("verify_results") == []


def test_runtime_execute_json(tmp_path):
    path = tmp_path / "proc.json"
    path.write_text(
        json.dumps(
            {
                "steps": [{"action": "wait", "ms": 1}],
                "return": {"success": True, "via": "json"},
            }
        )
    )
    h = Harness()

    @h.register_action("wait")
    def wait(ms: int):
        return {"slept_ms": ms}

    rt = Runtime(harness=h)
    result = rt.execute_file(path)
    assert result["success"] is True
    assert result["via"] == "json"
    assert result.get("verify_results") == []


def test_load_steps_file_missing_steps(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{}")
    with pytest.raises(StepsFormatError, match="missing 'steps'"):
        load_steps_file(path)


def test_example_open_textedit_json_exists():
    example = Path(__file__).resolve().parent.parent / "examples" / "open_textedit.json"
    assert example.exists()
    doc = load_steps_file(example)
    assert len(doc["steps"]) >= 2
