"""Tests for case_mcp server tools (no stdio transport)."""

from __future__ import annotations

import pytest

mcp = pytest.importorskip("mcp")

from case_mcp.server import INSTRUCTIONS, create_mcp


@pytest.fixture
def server():
    return create_mcp()


def test_instructions_non_empty():
    assert "case_run" in INSTRUCTIONS
    assert "davinci" in INSTRUCTIONS


def test_case_help_lists_tools(server):
    fn = server._tool_manager._tools["case_help"].fn
    out = fn()
    assert "planner_guide" in out
    assert any(t["name"] == "case_run" for t in out["tools"])


def test_case_list_procedures(server):
    fn = server._tool_manager._tools["case_list_procedures"].fn
    out = fn()
    assert out["success"] is True
    names = {p["name"] for p in out["procedures"]}
    assert "davinci/import_and_append" in names


def test_case_list_verifiers(server):
    fn = server._tool_manager._tools["case_list_verifiers"].fn
    out = fn()
    assert "davinci.db_delta" in out["verifiers"]


def test_case_doctor(server):
    fn = server._tool_manager._tools["case_doctor"].fn
    out = fn()
    assert "permissions" in out
    assert "procedure_dirs" in out


def test_case_run_unknown_procedure(server):
    fn = server._tool_manager._tools["case_run"].fn
    out = fn(
        procedure="definitely_not_a_real_procedure_xyz",
        check_permissions=False,
    )
    assert out["success"] is False
    assert out["code"] == "E_NOT_FOUND"


def test_case_run_json_procedure(server, tmp_path):
    import json

    from case_sdk.harness.executor import Harness

    proc = tmp_path / "t.json"
    proc.write_text(
        json.dumps(
            {"steps": [{"action": "wait", "ms": 1}], "return": {"success": True}}
        ),
        encoding="utf-8",
    )

    fake = Harness()

    @fake.register_action("wait")
    def wait(ms: int):
        return {"slept_ms": ms}

    with pytest.MonkeyPatch.context() as mp:
        from case_sdk import Runtime

        mp.setattr(
            "case_mcp.server._runtime",
            lambda verify_mode=None: Runtime(harness=fake, verify_mode=verify_mode),
        )
        fn = server._tool_manager._tools["case_run"].fn
        out = fn(
            procedure=str(proc),
            params={},
            check_permissions=False,
        )
    assert out["success"] is True
    assert out["result"].get("success") is True


def test_case_list_procedures_surfaces_user_action(server):
    fn = server._tool_manager._tools["case_list_procedures"].fn
    out = fn()
    entries = {e["name"]: e for e in out["procedures"]}
    item = entries.get("davinci/append_to_timeline")
    assert item is not None
    assert item.get("requires_user_action") is True
    assert "Media Pool" in item.get("user_action_prompt", "")


def test_case_run_user_action_required_without_confirmation(server, tmp_path):
    import json

    proc = tmp_path / "needs.json"
    proc.write_text(
        json.dumps(
            {
                "requires_user_action": True,
                "user_action_prompt": "Click the thing",
                "steps": [{"action": "wait", "ms": 1}],
                "return": {"success": True},
            }
        ),
        encoding="utf-8",
    )

    fn = server._tool_manager._tools["case_run"].fn
    out = fn(
        procedure=str(proc),
        params={},
        check_permissions=False,
    )
    assert out["success"] is False
    assert out["code"] == "E_USER_ACTION_REQUIRED"
    assert "Click the thing" in out["prompt"]


def test_case_run_user_action_confirmed_executes(server, tmp_path):
    import json

    from case_sdk.harness.executor import Harness

    proc = tmp_path / "needs.json"
    proc.write_text(
        json.dumps(
            {
                "requires_user_action": True,
                "user_action_prompt": "Click the thing",
                "steps": [{"action": "wait", "ms": 1}],
                "return": {"success": True},
            }
        ),
        encoding="utf-8",
    )

    fake = Harness()

    @fake.register_action("wait")
    def wait(ms: int):
        return {"slept_ms": ms}

    with pytest.MonkeyPatch.context() as mp:
        from case_sdk import Runtime

        mp.setattr(
            "case_mcp.server._runtime",
            lambda verify_mode=None: Runtime(harness=fake, verify_mode=verify_mode),
        )
        fn = server._tool_manager._tools["case_run"].fn
        out = fn(
            procedure=str(proc),
            params={},
            check_permissions=False,
            user_action_confirmed=True,
        )
    assert out["success"] is True
    assert out["result"].get("success") is True
