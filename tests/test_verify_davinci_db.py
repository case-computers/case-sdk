"""Tests for DaVinci Project.db helpers (tmp SQLite, no Resolve required)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from case_sdk.apps.davinci import db as dbmod


def _make_project_db(tmp_path: Path, project_name: str = "TestProject") -> Path:
    project_dir = tmp_path / project_name
    project_dir.mkdir()
    db_path = project_dir / "Project.db"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE Sm2TiItem ("
            " DbType TEXT, Name TEXT, Start INTEGER,"
            " Duration INTEGER, MediaFilePath TEXT)"
        )
        cur.execute(
            "CREATE TABLE 'Sm2MpFolder_Sm2MpMedia' (FolderId INTEGER, MediaId INTEGER)"
        )
        cur.execute("CREATE TABLE Sm2MpMedia (Name TEXT)")
        cur.execute("CREATE TABLE Sm2TiCompositionTable (Id INTEGER)")
        cur.execute("CREATE TABLE FontRenderItem (Id INTEGER)")
        cur.execute("CREATE TABLE Sm2TiTrack (Id INTEGER)")
        cur.execute("CREATE TABLE Sm2Sequence (Id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    return db_path


def _insert(db_path: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_snapshot_empty(tmp_path):
    db = _make_project_db(tmp_path)
    snap = dbmod.snapshot(db)
    assert snap.timeline_video == 0
    assert snap.project_name == "TestProject"


def test_snapshot_counts(tmp_path):
    db = _make_project_db(tmp_path)
    _insert(db, "INSERT INTO Sm2TiItem (DbType, Name) VALUES (?, ?)", ("Sm2TiVideoClip", "v1"))
    _insert(db, "INSERT INTO Sm2TiItem (DbType, Name) VALUES (?, ?)", ("Sm2TiVideoClip", "v2"))
    snap = dbmod.snapshot(db)
    assert snap.timeline_video == 2


def test_poll_for_count_delta_detects_growth(tmp_path):
    db = _make_project_db(tmp_path)
    baseline = dbmod.snapshot(db)

    def _add_clip():
        time.sleep(0.3)
        _insert(
            db,
            "INSERT INTO Sm2TiItem (DbType, Name) VALUES (?, ?)",
            ("Sm2TiVideoClip", "late"),
        )

    threading.Thread(target=_add_clip, daemon=True).start()
    ok, latest = dbmod.poll_for_count_delta(
        "timeline_video", baseline, min_delta=1, timeout=3.0, db_path=db
    )
    assert ok is True
    assert latest is not None
    assert latest.timeline_video == 1


def test_media_pool_name_exists(tmp_path):
    db = _make_project_db(tmp_path)
    _insert(db, "INSERT INTO Sm2MpMedia (Name) VALUES (?)", ("demo-1.mov",))
    assert dbmod.media_pool_name_exists("demo-1", db_path=db) is True
    assert dbmod.media_pool_name_exists("missing", db_path=db) is False
