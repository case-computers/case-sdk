"""DaVinci-specific AX walking helpers.

Uses raw PyObjC ``ApplicationServices`` instead of atomacos's NativeUIElement
wrapper. atomacos misses most of Resolve's Qt-rendered internal tree (Color
Wheels panel, Inspector numerics, font combos) — calling
``AXUIElementCopyAttributeValue`` directly exposes the elements Resolve
actually publishes via AX.

A short-lived per-harness cache holds the parsed Color page panel snapshot so
consecutive helper calls inside one procedure don't re-walk the whole AX
tree. TTL is 2 s — long enough to cover a chained ``set_temp`` + ``set_tint``
within one tenth of a second, short enough that a panel-mode switch is picked
up on the next call.
"""

from __future__ import annotations

import time
from typing import Any

# PyObjC modules are macOS-only. We delay the imports to runtime (inside the
# helper functions) so the module is importable on linux — the actions that
# actually use AX are gated by the parent ``__init__.py``'s try/except, but
# downstream `from case_sdk.apps.davinci import _ax` calls (test mocks, etc.)
# would still crash at module load on linux without this lazy boundary.
try:  # pragma: no cover — macOS-only
    import ApplicationServices as AX
    import Cocoa
except ImportError:  # pragma: no cover — non-macOS path
    AX = None  # type: ignore[assignment]
    Cocoa = None  # type: ignore[assignment]

_RESOLVE_APP_NAME_NEEDLE = "davinci resolve"


# ---- alias maps (verbatim from EmulatedHumanoid/video_editor/resolve_agent.py)

# Resolve abbreviates labels per panel mode. In "Log Wheels" mode "Contrast"
# becomes "Cont", "Mid/Detail" becomes "Mid Details", etc. Procedure JSON
# always uses the canonical name; this map provides the fallbacks.
LABEL_ALIASES: dict[str, list[str]] = {
    "contrast": ["cont"],
    "mid/detail": ["mid details", "mid detail"],
    "mid details": ["mid/detail", "mid detail"],
    "color boost": ["boost"],
    "saturation": ["sat"],
    "highlights": ["highs", "hi"],
    "shadows": ["shads", "lows"],
}

# In "Log Wheels" mode the four colour wheels are renamed but anchored the
# same way. Map canonical → mode equivalents so a procedure written for
# "Color Wheels" mode still resolves under "Log Wheels".
WHEEL_ALIASES: dict[str, list[str]] = {
    "lift": ["shadow"],
    "gamma": ["midtone"],
    "gain": ["highlights", "highlight"],
    "offset": [],
}

# Slider labels in the bottom Log-Wheels row that collide with a wheel label
# of the same name. For these, look-up picks the LOWER occurrence (largest Y)
# — the slider sits below the wheel.
BOTTOM_ROW_LABELS = {"highlights", "shadows"}


# ---- low-level AX primitives (raw ApplicationServices) ------------------


def _safe_attr(elem: Any, name: str) -> Any:
    """Read an AX attribute via ``AXUIElementCopyAttributeValue``.

    Returns ``None`` on any AX error so callers can branch defensively
    instead of catching exceptions everywhere.
    """
    try:
        err, val = AX.AXUIElementCopyAttributeValue(elem, name, None)
        return val if err == 0 else None
    except Exception:
        return None


def _safe_children(elem: Any) -> list[Any]:
    """Return the raw ``AXChildren`` list (empty on error)."""
    children = _safe_attr(elem, "AXChildren")
    return list(children) if children else []


def _ax_point(elem: Any) -> tuple[float | None, float | None]:
    """Return (x, y) centre of an AX element, or (None, None) if unresolvable.

    Reads ``AXPosition`` + ``AXSize`` and unwraps them via
    ``AXValueGetValue``. Falls back to ``AXFrame`` (a CGRect) if either
    AXPosition or AXSize is missing — some Resolve nodes only publish AXFrame.
    """
    pos = _safe_attr(elem, "AXPosition")
    size = _safe_attr(elem, "AXSize")
    if pos is not None and size is not None:
        try:
            _, pt = AX.AXValueGetValue(
                pos, getattr(AX, "kAXValueCGPointType", 1), None
            )
            _, sz = AX.AXValueGetValue(
                size, getattr(AX, "kAXValueCGSizeType", 2), None
            )
            if pt is not None and sz is not None and sz.width and sz.height:
                return float(pt.x) + float(sz.width) / 2.0, float(pt.y) + float(sz.height) / 2.0
        except Exception:
            pass

    frame = _safe_attr(elem, "AXFrame")
    if frame is None:
        return None, None
    try:
        _, rect = AX.AXValueGetValue(
            frame, getattr(AX, "kAXValueCGRectType", 3), None
        )
        if rect is None:
            return None, None
        return (
            float(rect.origin.x) + float(rect.size.width) / 2.0,
            float(rect.origin.y) + float(rect.size.height) / 2.0,
        )
    except Exception:
        return None, None


def _get_resolve_app() -> Any:
    """Return the raw ``AXUIElementRef`` for the DaVinci Resolve app.

    Walks NSWorkspace's running-apps list, finds Resolve by
    case-insensitive localizedName match, and binds an AX element to its
    PID. Raises if Resolve isn't running.
    """
    workspace = Cocoa.NSWorkspace.sharedWorkspace()
    for app in workspace.runningApplications():
        try:
            name = (app.localizedName() or "").lower()
        except Exception:
            continue
        if _RESOLVE_APP_NAME_NEEDLE in name:
            try:
                pid = int(app.processIdentifier())
            except Exception:
                continue
            return AX.AXUIElementCreateApplication(pid)
    raise RuntimeError(
        "DaVinci Resolve is not running. Launch Resolve before invoking "
        "any davinci_* action."
    )


def _initial_queue(app_el: Any) -> list[Any]:
    """Return the BFS starting set for the AX walk.

    An AXApplication element exposes its windows through ``AXMainWindow`` /
    ``AXFocusedWindow`` / ``AXWindows`` rather than as children of
    ``AXChildren`` (which typically only holds the AXMenuBar). Seed the
    queue with the app and its windows so the BFS reaches every panel.
    """
    queue: list[Any] = [app_el]
    seen_ids: set[int] = {id(app_el)}

    main = _safe_attr(app_el, "AXMainWindow")
    if main is not None and id(main) not in seen_ids:
        queue.append(main)
        seen_ids.add(id(main))

    focused = _safe_attr(app_el, "AXFocusedWindow")
    if focused is not None and id(focused) not in seen_ids:
        queue.append(focused)
        seen_ids.add(id(focused))

    for win in (_safe_attr(app_el, "AXWindows") or []):
        if id(win) not in seen_ids:
            queue.append(win)
            seen_ids.add(id(win))
    return queue


# ---- panel scan + cache -------------------------------------------------


# Per-harness cache. Module-level dict keyed by id(harness). Entries cleared
# implicitly when the harness is GC'd.
_SCAN_CACHE: dict[int, dict[str, Any]] = {}
_SCAN_TTL = 2.0  # seconds


def scan_color_panel(app_el: Any) -> tuple[dict[str, list[tuple[float, float, Any]]], list[tuple[float, float, Any]]]:
    """BFS the AX tree once and split every ``AXStaticText`` into two buckets.

    Returns:
        labels: dict mapping lower-cased non-numeric text → list of (x, y, el)
        numerics: list of (x, y, el) for elements whose AXValue parses as float
    """
    labels: dict[str, list[tuple[float, float, Any]]] = {}
    numerics: list[tuple[float, float, Any]] = []
    queue = _initial_queue(app_el)
    seen = 0
    # Hard cap to keep the walk bounded if AX returns a cyclic tree.
    while queue and seen < 200_000:
        el = queue.pop(0)
        seen += 1
        role = _safe_attr(el, "AXRole") or ""
        if role == "AXStaticText":
            raw = _safe_attr(el, "AXValue") or ""
            val = raw.strip() if isinstance(raw, str) else str(raw).strip()
            ex, ey = _ax_point(el)
            if ex is not None and val:
                try:
                    float(val)
                except (ValueError, TypeError):
                    labels.setdefault(val.lower(), []).append((ex, ey, el))
                else:
                    numerics.append((ex, ey, el))
        for child in _safe_children(el):
            queue.append(child)
    return labels, numerics


def scan_panel_cached(
    harness_key: int,
    *,
    force: bool = False,
) -> tuple[dict[str, list[tuple[float, float, Any]]], list[tuple[float, float, Any]]]:
    """Return (labels, numerics) from cache, rescanning if expired or forced."""
    entry = _SCAN_CACHE.get(harness_key)
    now = time.monotonic()
    if force or entry is None or (now - entry["ts"]) > _SCAN_TTL:
        labels, numerics = scan_color_panel(_get_resolve_app())
        entry = {"labels": labels, "numerics": numerics, "ts": now}
        _SCAN_CACHE[harness_key] = entry
    return entry["labels"], entry["numerics"]


def invalidate_scan(harness_key: int) -> None:
    """Drop the cache for a harness — call after an action that repaints the panel."""
    _SCAN_CACHE.pop(harness_key, None)


# ---- label/numeric lookup -----------------------------------------------


def find_numeric_neighbor(harness_key: int, label_text: str) -> Any:
    """Return the numeric AXStaticText immediately right of *label_text*.

    Matches case-insensitively against the canonical name, falling back to
    ``LABEL_ALIASES`` for mode-specific abbreviations (Log Wheels → "Cont"
    for "Contrast" etc.). Raises ``RuntimeError`` if the label or its
    numeric neighbour can't be located on the active Resolve window.
    """
    canonical = label_text.strip().lower()
    candidates = [canonical] + LABEL_ALIASES.get(canonical, [])

    labels, numerics = scan_panel_cached(harness_key)
    matches: list[tuple[float, float, Any]] = []
    matched_name = canonical
    for needle in candidates:
        matches = labels.get(needle, [])
        if matches:
            matched_name = needle
            break

    if not matches:
        labels, numerics = scan_panel_cached(harness_key, force=True)
        for needle in candidates:
            matches = labels.get(needle, [])
            if matches:
                matched_name = needle
                break

    if not matches:
        tried = ", ".join(repr(n) for n in candidates)
        raise RuntimeError(
            f"Label {label_text!r} not found on Color page (tried {tried}). "
            "Open the Color page and verify the Primaries panel is in "
            "Color Wheels mode."
        )

    if matched_name in BOTTOM_ROW_LABELS and len(matches) > 1:
        matches.sort(key=lambda t: t[1], reverse=True)
    else:
        matches.sort(key=lambda t: t[1])
    label_x, label_y, _ = matches[0]

    best_el: Any = None
    best_x = float("inf")
    for ex, ey, el in numerics:
        if abs(ey - label_y) < 10 and ex > label_x and ex < best_x:
            best_el = el
            best_x = ex
    if best_el is None:
        raise RuntimeError(
            f"Numeric value for {label_text!r} (matched as {matched_name!r}) "
            "not found on Color page"
        )
    return best_el


# ---- wheel-reset cluster ------------------------------------------------


def find_wheel_reset_buttons() -> list[Any]:
    """Locate the 4 colour-wheel Reset buttons via Y-axis clustering.

    The Color page lays out Lift/Gamma/Gain/Offset reset buttons on a single
    row. We collect every ``AXButton`` whose ``AXDescription`` is "reset",
    cluster by Y (±10 px), pick the cluster of 4 (or fallback to the largest
    cluster), then sort by X. Returns
    ``[lift, gamma, gain, offset]`` AX elements.
    """
    app_el = _get_resolve_app()
    resets: list[tuple[float, float, Any]] = []
    queue = _initial_queue(app_el)
    seen = 0
    while queue and seen < 200_000:
        el = queue.pop(0)
        seen += 1
        if (_safe_attr(el, "AXRole") or "") == "AXButton":
            desc = _safe_attr(el, "AXDescription") or ""
            if isinstance(desc, str) and desc.strip().lower() == "reset":
                ex, ey = _ax_point(el)
                if ex is not None and ey is not None:
                    resets.append((ex, ey, el))
        for child in _safe_children(el):
            queue.append(child)

    if not resets:
        return []

    resets.sort(key=lambda t: t[1])
    clusters: list[list[tuple[float, float, Any]]] = [[resets[0]]]
    for r in resets[1:]:
        if abs(r[1] - clusters[-1][-1][1]) <= 10:
            clusters[-1].append(r)
        else:
            clusters.append([r])

    wheel_cluster = next((c for c in clusters if len(c) == 4), None)
    if wheel_cluster is None:
        wheel_cluster = max(clusters, key=len)

    wheel_cluster.sort(key=lambda t: t[0])
    return [t[2] for t in wheel_cluster]


# ---- combo box lookup ---------------------------------------------------


def find_combo_box(label: str) -> Any:
    """Return the first ``AXComboBox`` matching *label* (case-insensitive substring)."""
    needle = label.strip().lower()
    app_el = _get_resolve_app()
    queue = _initial_queue(app_el)
    seen = 0
    while queue and seen < 200_000:
        el = queue.pop(0)
        seen += 1
        if (_safe_attr(el, "AXRole") or "") == "AXComboBox":
            title = _safe_attr(el, "AXTitle") or ""
            desc = _safe_attr(el, "AXDescription") or ""
            t_low = title.strip().lower() if isinstance(title, str) else ""
            d_low = desc.strip().lower() if isinstance(desc, str) else ""
            if needle in t_low or needle in d_low:
                return el
        for child in _safe_children(el):
            queue.append(child)
    return None


# ---- media-pool search field -------------------------------------------


def find_media_pool_search() -> Any:
    """Return the Media Pool's search ``AXTextField``, enabling its toggle if hidden."""
    app_el = _get_resolve_app()

    def _walk_for(role: str, desc_needle: str) -> Any:
        d_needle = desc_needle.strip().lower()
        queue = _initial_queue(app_el)
        seen = 0
        while queue and seen < 200_000:
            el = queue.pop(0)
            seen += 1
            if (_safe_attr(el, "AXRole") or "") == role:
                desc = _safe_attr(el, "AXDescription") or ""
                title = _safe_attr(el, "AXTitle") or ""
                d_low = desc.strip().lower() if isinstance(desc, str) else ""
                t_low = title.strip().lower() if isinstance(title, str) else ""
                if d_needle in d_low or d_needle in t_low:
                    return el
            for child in _safe_children(el):
                queue.append(child)
        return None

    field = _walk_for("AXTextField", "search")
    if field is not None:
        return field

    toggle = _walk_for("AXCheckBox", "search")
    if toggle is not None:
        val = _safe_attr(toggle, "AXValue")
        val_str = str(val) if val is not None else ""
        if val_str in ("0", "False", "0.0"):
            try:
                AX.AXUIElementPerformAction(toggle, "AXPress")
            except Exception:
                return None
            time.sleep(0.4)
            return _walk_for("AXTextField", "search")
    return None
