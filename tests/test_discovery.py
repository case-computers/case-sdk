"""Tests for procedure discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from case_sdk.discovery import (
    ProcedureNotFound,
    list_procedures,
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
