"""DaVinci Resolve verifiers (Project.db ground truth)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from case_sdk.apps.davinci import db as davinci_db
from case_sdk.verify.context import VerifyContext
from case_sdk.verify.runner import VerifyError, register

_SNAPSHOT_FIELDS = frozenset(
    {
        "timeline_video",
        "timeline_audio",
        "timeline_transitions",
        "media_pool_items",
        "compositions",
        "font_render_items",
        "tracks",
        "sequences",
    }
)


def _resolve_db_path(args: dict[str, Any], ctx: VerifyContext) -> Path | None:
    hint = args.get("project_hint")
    if hint:
        return davinci_db.pick_active_db(str(hint))
    if ctx.last_db_path is not None:
        return Path(ctx.last_db_path)
    return davinci_db.pick_active_db()


def _store_snapshot(
    snap: davinci_db.Snapshot, args: dict[str, Any], ctx: VerifyContext
) -> dict[str, Any]:
    ctx.last_snapshot = snap
    ctx.last_db_path = snap.db_path
    label = args.get("as") or args.get("baseline")
    if label:
        ctx.baselines[str(label)] = snap
    return {
        "ok": True,
        "project": snap.project_name,
        "snapshot": snap.to_dict(),
    }


@register("davinci.db_snapshot")
def verify_db_snapshot(args: dict[str, Any], ctx: VerifyContext) -> dict[str, Any]:
    """Capture a Project.db row-count snapshot (optional ``as`` name)."""
    db_path = _resolve_db_path(args, ctx)
    if db_path is None:
        return {"ok": False, "reason": "no_project_db_found"}
    try:
        snap = davinci_db.snapshot(db_path)
    except Exception as exc:
        return {"ok": False, "reason": f"snapshot_failed: {exc}"}
    return _store_snapshot(snap, args, ctx)


@register("davinci.db_delta")
def verify_db_delta(args: dict[str, Any], ctx: VerifyContext) -> dict[str, Any]:
    """Poll until a snapshot field changes vs a baseline.

    Args:
        field: Snapshot attribute (e.g. ``timeline_video``).
        min_delta: minimum increase (default 1). Use with positive growth.
        max_delta: maximum change (e.g. -1 for delete: set ``min_delta`` omitted
            and use ``max_delta: -1`` meaning delta <= -1).
        baseline: named baseline from ``db_snapshot`` (default: ``last`` snapshot).
        timeout: poll seconds (default 5).
    """
    field = args.get("field")
    if not field or field not in _SNAPSHOT_FIELDS:
        raise VerifyError(
            f"field must be one of {sorted(_SNAPSHOT_FIELDS)}, got {field!r}"
        )

    baseline_key = args.get("baseline", "last")
    if baseline_key == "last":
        baseline = ctx.last_snapshot
    else:
        baseline = ctx.baselines.get(str(baseline_key))
    if baseline is None:
        return {
            "ok": False,
            "reason": f"no baseline {baseline_key!r}; run davinci.db_snapshot first",
        }

    db_path = _resolve_db_path(args, ctx)
    if db_path is None:
        db_path = Path(baseline.db_path)

    timeout = float(args.get("timeout", 5.0))
    min_delta = args.get("min_delta")
    max_delta = args.get("max_delta")

    if max_delta is not None:
        # Poll until field decreased by at least abs(max_delta)
        target = int(max_delta)
        ok, latest = _poll_until_delta(
            field, baseline, db_path, timeout, want_decrease=abs(target)
        )
    else:
        md = int(min_delta if min_delta is not None else 1)
        ok, latest = davinci_db.poll_for_count_delta(
            field,
            baseline,
            min_delta=md,
            timeout=timeout,
            db_path=db_path,
        )

    if latest is not None:
        ctx.last_snapshot = latest
        ctx.last_db_path = latest.db_path

    if ok:
        delta = getattr(latest, field) - getattr(baseline, field) if latest else 0
        return {
            "ok": True,
            "field": field,
            "delta": delta,
            "baseline": baseline.to_dict() if hasattr(baseline, "to_dict") else {},
            "latest": latest.to_dict() if latest else {},
        }

    pre = getattr(baseline, field)
    post = getattr(latest, field) if latest else pre
    return {
        "ok": False,
        "reason": f"{field} delta not observed within {timeout}s (pre={pre} post={post})",
        "field": field,
        "pre": pre,
        "post": post,
    }


def _poll_until_delta(
    field: str,
    baseline: davinci_db.Snapshot,
    db_path: Path,
    timeout: float,
    *,
    want_decrease: int,
) -> tuple[bool, davinci_db.Snapshot | None]:
    """Poll until field decreased by at least ``want_decrease``."""
    import time

    latest: davinci_db.Snapshot | None = None
    start = time.monotonic()
    baseline_value = getattr(baseline, field)
    while True:
        try:
            latest = davinci_db.snapshot(db_path)
        except Exception:
            pass
        else:
            current = getattr(latest, field)
            if current != -1 and (baseline_value - current) >= want_decrease:
                return True, latest
        if time.monotonic() - start >= timeout:
            return False, latest
        time.sleep(0.25)


@register("davinci.media_pool_name")
def verify_media_pool_name(args: dict[str, Any], ctx: VerifyContext) -> dict[str, Any]:
    """Poll until a media pool clip name appears (substring match)."""
    name = args.get("name") or args.get("clip_name")
    if not name:
        raise VerifyError("name or clip_name is required")

    db_path = _resolve_db_path(args, ctx)
    timeout = float(args.get("timeout", 5.0))
    ok = davinci_db.wait_for_media_pool_name(
        str(name), timeout=timeout, db_path=db_path
    )
    if ok:
        return {"ok": True, "name": name}
    return {
        "ok": False,
        "reason": f"media pool name {name!r} not found within {timeout}s",
    }


@register("davinci.timeline_clips")
def verify_timeline_clips(args: dict[str, Any], ctx: VerifyContext) -> dict[str, Any]:
    """Read-only: list timeline items (fails if no DB). Not a delta check."""
    db_path = _resolve_db_path(args, ctx)
    if db_path is None:
        return {"ok": False, "reason": "no_project_db_found", "clips": []}
    limit = int(args.get("limit", 50))
    try:
        clips = davinci_db.timeline_items(db_path, limit=limit)
    except Exception as exc:
        return {"ok": False, "reason": f"db_read_failed: {exc}", "clips": []}
    min_clips = args.get("min_clips")
    if min_clips is not None and len(clips) < int(min_clips):
        return {
            "ok": False,
            "reason": f"expected at least {min_clips} clips, got {len(clips)}",
            "clips": clips,
            "project": db_path.parent.name,
        }
    return {
        "ok": True,
        "clips": clips,
        "project": db_path.parent.name,
        "count": len(clips),
    }


# Handlers register at def-time via the @register decorator.
