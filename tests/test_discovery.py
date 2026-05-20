"""Tests for procedure discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from case_sdk.discovery import (
    ProcedureNotFound,
    list_procedures,
    read_procedure_metadata,
    resolve_procedure,
)


def test_resolve_builtin_append():
    path = resolve_procedure("davinci/append_to_timeline")
    assert path.suffix == ".json"
    assert path.name == "append_to_timeline.json"
    doc = json.loads(path.read_text())
    assert "steps" in doc


def test_user_override_wins(tmp_path, monkeypatch):
    user_dir = tmp_path / "procedures"
    user_dir.mkdir()
    custom = user_dir / "davinci"
    custom.mkdir()
    (custom / "append_to_timeline.json").write_text(
        json.dumps({"steps": [], "return": {"custom": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CASE_SDK_PROCEDURES_PATH", str(user_dir))
    path = resolve_procedure("davinci/append_to_timeline")
    assert json.loads(path.read_text())["return"]["custom"] is True


def test_list_includes_builtin():
    names = {e["name"] for e in list_procedures()}
    assert "davinci/import_and_append" in names


def test_unknown_raises():
    with pytest.raises(ProcedureNotFound):
        resolve_procedure("definitely_not_a_procedure_xyz")


def test_read_metadata_json_with_user_action(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(
        json.dumps(
            {
                "description": "needs click",
                "verify_mode": "hard",
                "requires_user_action": True,
                "user_action_prompt": "Click the thing first",
                "steps": [],
            }
        )
    )
    meta = read_procedure_metadata(p)
    assert meta["description"] == "needs click"
    assert meta["verify_mode"] == "hard"
    assert meta["requires_user_action"] is True
    assert meta["user_action_prompt"] == "Click the thing first"


def test_read_metadata_default_when_no_user_action(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"description": "noop", "steps": []}))
    meta = read_procedure_metadata(p)
    assert meta["requires_user_action"] is False
    assert "user_action_prompt" not in meta


def test_read_metadata_py_file_returns_default(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("def procedure(params):\n    return {}\n    yield\n")
    meta = read_procedure_metadata(p)
    assert meta == {"requires_user_action": False}


def test_read_metadata_malformed_json_swallowed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json at all {")
    meta = read_procedure_metadata(p)
    assert meta == {"requires_user_action": False}


def test_list_includes_user_action_fields(tmp_path, monkeypatch):
    user_dir = tmp_path / "procedures" / "mypack"
    user_dir.mkdir(parents=True)
    (user_dir / "needs_click.json").write_text(
        json.dumps(
            {
                "description": "manual",
                "requires_user_action": True,
                "user_action_prompt": "Select before continuing",
                "steps": [{"action": "wait", "ms": 1}],
            }
        )
    )
    monkeypatch.setenv("CASE_SDK_PROCEDURES_PATH", str(tmp_path / "procedures"))
    entries = {e["name"]: e for e in list_procedures()}
    item = entries.get("mypack/needs_click")
    assert item is not None
    assert item.get("requires_user_action") is True
    assert item.get("user_action_prompt") == "Select before continuing"


def test_builtin_append_to_timeline_declares_user_action():
    entries = {e["name"]: e for e in list_procedures()}
    item = entries.get("davinci/append_to_timeline")
    assert item is not None
    assert item.get("requires_user_action") is True
    assert "Media Pool" in item.get("user_action_prompt", "")


def test_symlink_escaping_search_root_is_skipped(tmp_path, monkeypatch):
    """A symlink whose target lives outside the search root must be rejected."""
    user_dir = tmp_path / "procedures"
    user_dir.mkdir()
    real_target = tmp_path / "outside.json"
    real_target.write_text(json.dumps({"steps": [{"action": "wait", "ms": 1}]}))
    symlink = user_dir / "evil.json"
    symlink.symlink_to(real_target)

    monkeypatch.setenv("CASE_SDK_PROCEDURES_PATH", str(user_dir))
    with pytest.warns(RuntimeWarning, match="outside search root"):
        entries = list_procedures()
    names = {e["name"] for e in entries}
    assert "evil" not in names


def test_user_shadow_of_builtin_emits_warning(tmp_path, monkeypatch):
    user_dir = tmp_path / "procedures" / "davinci"
    user_dir.mkdir(parents=True)
    (user_dir / "focus_resolve.json").write_text(
        json.dumps({"steps": [{"action": "wait", "ms": 1}]})
    )
    monkeypatch.setenv(
        "CASE_SDK_PROCEDURES_PATH", str(tmp_path / "procedures")
    )
    with pytest.warns(RuntimeWarning, match="built-in procedure 'davinci/focus_resolve'"):
        entries = list_procedures()
    # User file still wins.
    match = next(e for e in entries if e["name"] == "davinci/focus_resolve")
    assert str(tmp_path) in match["path"]
