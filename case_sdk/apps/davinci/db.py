"""Read-only client for DaVinci Resolve's Project.db.

Used by :mod:`case_sdk.verify` to confirm GUI actions landed. Schema is
reverse-engineered from Resolve Lite / Studio installs (Sm2* tables).
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

_BASE_DIRS = [
    HOME
    / "Library/Containers/com.blackmagic-design.DaVinciResolveLite/Data/"
    "Library/Application Support/Resolve Project Library/Resolve Projects",
    HOME
    / "Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Resolve Disk Database/Resolve Projects",
]


@dataclass
class Snapshot:
    project_name: str
    db_path: str
    timeline_video: int
    timeline_audio: int
    timeline_transitions: int
    media_pool_items: int
    compositions: int
    font_render_items: int
    tracks: int
    sequences: int
    captured_at: float

    def delta(self, other: "Snapshot") -> dict[str, int]:
        return {
            "timeline_video": self.timeline_video - other.timeline_video,
            "timeline_audio": self.timeline_audio - other.timeline_audio,
            "timeline_transitions": self.timeline_transitions - other.timeline_transitions,
            "media_pool_items": self.media_pool_items - other.media_pool_items,
            "compositions": self.compositions - other.compositions,
            "font_render_items": self.font_render_items - other.font_render_items,
            "tracks": self.tracks - other.tracks,
            "sequences": self.sequences - other.sequences,
        }

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "db_path": self.db_path,
            "timeline_video": self.timeline_video,
            "timeline_audio": self.timeline_audio,
            "timeline_transitions": self.timeline_transitions,
            "media_pool_items": self.media_pool_items,
            "compositions": self.compositions,
            "font_render_items": self.font_render_items,
            "tracks": self.tracks,
            "sequences": self.sequences,
            "captured_at": self.captured_at,
        }


def find_all_dbs() -> list[Path]:
    out: list[Path] = []
    for base in _BASE_DIRS:
        if not base.exists():
            continue
        out.extend(sorted(base.glob("Users/*/Projects/*/Project.db")))
    return out


def pick_active_db(window_title_hint: str | None = None) -> Path | None:
    dbs = find_all_dbs()
    if not dbs:
        return None
    if window_title_hint:
        h = window_title_hint.strip().lower()
        for p in dbs:
            project_name = p.parent.name.lower()
            if project_name == h or project_name in h or h in project_name:
                return p
    return max(dbs, key=lambda p: p.stat().st_mtime)


def _open_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)


def _count(conn: sqlite3.Connection, sql: str) -> int:
    try:
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return -1


def snapshot(db_path: Path) -> Snapshot:
    project_name = db_path.parent.name
    conn = _open_ro(db_path)
    try:
        return Snapshot(
            project_name=project_name,
            db_path=str(db_path),
            timeline_video=_count(
                conn, "SELECT COUNT(*) FROM Sm2TiItem WHERE DbType='Sm2TiVideoClip'"
            ),
            timeline_audio=_count(
                conn, "SELECT COUNT(*) FROM Sm2TiItem WHERE DbType='Sm2TiAudioClip'"
            ),
            timeline_transitions=_count(
                conn, "SELECT COUNT(*) FROM Sm2TiItem WHERE DbType='Sm2TiTransition'"
            ),
            media_pool_items=_count(conn, "SELECT COUNT(*) FROM 'Sm2MpFolder_Sm2MpMedia'"),
            compositions=_count(conn, "SELECT COUNT(*) FROM Sm2TiCompositionTable"),
            font_render_items=_count(conn, "SELECT COUNT(*) FROM FontRenderItem"),
            tracks=_count(conn, "SELECT COUNT(*) FROM Sm2TiTrack"),
            sequences=_count(conn, "SELECT COUNT(*) FROM Sm2Sequence"),
            captured_at=time.monotonic(),
        )
    finally:
        conn.close()


def timeline_items(db_path: Path, limit: int = 20) -> list[dict]:
    conn = _open_ro(db_path)
    try:
        rows = conn.execute(
            """SELECT DbType, Name, Start, Duration, MediaFilePath
               FROM Sm2TiItem
               ORDER BY Start
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "dbtype": r[0],
            "name": r[1],
            "start": r[2],
            "duration": r[3],
            "media_path": r[4],
        }
        for r in rows
    ]


def poll_for_count_delta(
    field: str,
    baseline_snapshot: Snapshot,
    *,
    min_delta: int = 1,
    max_delta: int | None = None,
    timeout: float = 5.0,
    poll_interval: float = 0.25,
    db_path: Path | None = None,
) -> tuple[bool, Snapshot | None]:
    if db_path is None:
        db_path = pick_active_db()
    if db_path is None:
        return False, None

    baseline_value = getattr(baseline_snapshot, field)
    if baseline_value == -1:
        return False, None

    latest: Snapshot | None = None
    start = time.monotonic()
    while True:
        try:
            latest = snapshot(db_path)
        except sqlite3.Error:
            pass
        else:
            current_value = getattr(latest, field)
            if current_value == -1:
                pass
            else:
                delta = current_value - baseline_value
                if max_delta is not None:
                    if delta <= max_delta:
                        return True, latest
                elif delta >= min_delta:
                    return True, latest

        if (time.monotonic() - start) >= timeout:
            return False, latest
        time.sleep(poll_interval)


def wait_for_media_pool_name(
    name: str,
    *,
    timeout: float = 5.0,
    poll_interval: float = 0.25,
    db_path: Path | None = None,
) -> bool:
    if db_path is None:
        db_path = pick_active_db()
    if db_path is None:
        return False

    start = time.monotonic()
    while True:
        if media_pool_name_exists(name, db_path=db_path):
            return True
        if (time.monotonic() - start) >= timeout:
            return False
        time.sleep(poll_interval)


def media_pool_name_exists(name: str, db_path: Path | None = None) -> bool:
    if db_path is None:
        db_path = pick_active_db()
    if db_path is None:
        return False
    try:
        conn = _open_ro(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM Sm2MpMedia WHERE LOWER(Name) LIKE LOWER(?) LIMIT 1",
                (f"%{name}%",),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return row is not None
