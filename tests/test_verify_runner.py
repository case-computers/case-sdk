"""Tests for case_sdk.verify runner + JSON integration."""

from __future__ import annotations

import json

import pytest

from case_sdk import Action, Runtime
from case_sdk.harness.executor import Harness
from case_sdk.steps import steps_to_procedure
from case_sdk.verify import VerifyContext, list_verifiers, run_verify
from case_sdk.protocol import ClientError

from tests.test_verify_davinci_db import _insert, _make_project_db


def test_verifiers_registered():
    names = list_verifiers()
    assert "davinci.db_snapshot" in names
    assert "davinci.db_delta" in names


def test_db_snapshot_and_delta_soft(tmp_path):
    db = _make_project_db(tmp_path)
    ctx = VerifyContext(mode="soft")
    run_verify("davinci.db_snapshot", {"as": "t0", "db_path": str(db)}, ctx)
    _insert(
        db,
        "INSERT INTO Sm2TiItem (DbType, Name) VALUES (?, ?)",
        ("Sm2TiVideoClip", "v1"),
    )
    result = run_verify(
        "davinci.db_delta",
        {
            "field": "timeline_video",
            "baseline": "t0",
            "min_delta": 1,
            "timeout": 1.0,
            "db_path": str(db),
        },
        ctx,
    )
    assert result["ok"] is True


def test_db_delta_hard_raises(tmp_path):
    db = _make_project_db(tmp_path)
    ctx = VerifyContext(mode="hard")
    run_verify("davinci.db_snapshot", {"as": "t0", "db_path": str(db)}, ctx)
    with pytest.raises(ClientError) as exc:
        run_verify(
            "davinci.db_delta",
            {
                "field": "timeline_video",
                "baseline": "t0",
                "min_delta": 1,
                "timeout": 0.2,
                "db_path": str(db),
            },
            ctx,
        )
    assert exc.value.code == "E_VERIFY"


def test_json_procedure_with_verify_steps(tmp_path):
    db = _make_project_db(tmp_path)
    doc = {
        "verify_mode": "soft",
        "steps": [
            {"verify": "davinci.db_snapshot", "as": "t0", "db_path": str(db)},
            {"action": "add_test_clip"},
            {
                "verify": "davinci.db_delta",
                "field": "timeline_video",
                "baseline": "t0",
                "min_delta": 1,
                "timeout": 2.0,
                "db_path": str(db),
            },
        ],
        "return": {"success": True},
    }

    h = Harness()

    @h.register_action("add_test_clip")
    def add_test_clip():
        _insert(
            db,
            "INSERT INTO Sm2TiItem (DbType, Name) VALUES (?, ?)",
            ("Sm2TiVideoClip", "v1"),
        )
        return {}

    gen = steps_to_procedure(doc, verify_mode="soft")
    rt = Runtime(harness=h)
    result = rt.local_driver.execute(gen, {})
    assert result["success"] is True
    assert len(result.get("verify_results", [])) == 2
