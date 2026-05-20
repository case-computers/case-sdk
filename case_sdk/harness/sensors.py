"""Sensors — read-only state observations for procedures.

A procedure ``yield Sensor("name", ...)`` gets the dict returned by the
registered sensor sent back into the generator via ``.send()``. Sensors must
never mutate the world; if they do, route the side-effect through an Action.

Three are registered by default:

* ``window_title`` — frontmost app + window title via NSWorkspace / atomacos.
  Uses the ``threading.Thread + join(timeout=2.0)`` pattern from
  ``EmulatedHumanoid/world_model.py`` because the macOS AX call can hang
  indefinitely during app transitions.
* ``ax_value`` — read ``AXValue`` for a described element. v0 stub: returns
  ``{"value": ""}`` when nothing resolves.
* ``screenshot`` — capture (and optionally crop) the screen. Enforces an 8 KB
  inline limit; oversize payloads return ``{"value": "TOO_LARGE", "bytes": N}``
  and skip base64 encoding entirely.
"""

from __future__ import annotations

import base64
import io
import threading
from typing import Any

import pyautogui

from case_sdk.harness.executor import Harness


# 8 KB inline payload ceiling. See protocol doc — anything bigger should
# eventually move to a blob URL handled server-side. Out of scope here.
INLINE_BYTE_LIMIT = 8 * 1024

# Frontmost-app names whose value is misleading (matches EH world_model).
_BLACKLISTED_FRONTMOST = frozenset(
    {
        "Antigravity",
        "Notification Center",
        "Spotlight",
        "UserNotificationCenter",
        "universalAccessAuthWarn",
    }
)


def register_default_sensors(h: Harness) -> None:
    """Register the standard sensor set onto ``h``.

    Called from ``case_sdk.harness.gui.build_default_harness`` so consumers
    get one harness with both actions and sensors. Separating it out keeps
    the GUI primitive registrations easy to scan.
    """

    @h.register_sensor("window_title")
    def window_title() -> dict[str, Any]:
        return {"value": _read_window_title()}

    @h.register_sensor("ax_value")
    def ax_value(element_ref: dict[str, Any] | str | None = None) -> dict[str, Any]:
        return {"value": _read_ax_value(element_ref)}

    @h.register_sensor("screenshot")
    def screenshot(region: dict[str, Any] | None = None) -> dict[str, Any]:
        return _capture_screenshot(region)


# ---- window_title --------------------------------------------------------


def _read_window_title() -> str:
    """Return ``"<app name>"`` or ``"<app name> — <window title>"``.

    Falls back to ``""`` rather than raising — procedures can branch on the
    empty string without wrapping every sensor call in try/except.
    """
    app_name = _frontmost_app_name()
    if not app_name:
        return ""
    title = _frontmost_window_title()
    if title and title != app_name:
        return f"{app_name} — {title}"
    return app_name


def _frontmost_app_name() -> str:
    """Read the frontmost app's localized name via NSWorkspace.

    Fast and safe — no AX call, no thread dance. Returns ``""`` if NSWorkspace
    is unavailable (non-macOS, missing perms, etc.).
    """
    try:
        from AppKit import NSWorkspace  # noqa: WPS433 — lazy macOS-only import.
    except Exception:
        return ""
    try:
        workspace = NSWorkspace.sharedWorkspace()
        app = workspace.frontmostApplication()
        if app is None:
            return ""
        name = app.localizedName()
        if not name:
            return ""
        if name in _BLACKLISTED_FRONTMOST:
            return ""
        return str(name)
    except Exception:
        return ""


def _frontmost_window_title() -> str | None:
    """Try to read the frontmost window's AXTitle via atomacos.

    The AX call can hang during app transitions (a macOS quirk documented in
    ``EmulatedHumanoid/world_model.py``). To avoid taking the whole runtime
    down with us we run it on a daemon thread and bail after 2 seconds.
    """
    try:
        import atomacos  # noqa: WPS433
    except Exception:
        return None

    result: list[Any] = [None]

    def _query() -> None:
        try:
            app = atomacos.getFrontmostApp()
            wins = app.AXWindows
            if wins:
                result[0] = getattr(wins[0], "AXTitle", None)
        except Exception:
            result[0] = None

    t = threading.Thread(target=_query, daemon=True)
    t.start()
    t.join(timeout=2.0)
    title = result[0]
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


# ---- ax_value ------------------------------------------------------------


def _read_ax_value(element_ref: Any) -> str:
    """v0 stub: read AXValue from a described element on the frontmost app.

    Mirrors the resolver shape used by ``ax_press`` / ``ax_set_value`` so a
    procedure can use the same ``element_ref`` dict across actions and
    sensors. Returns ``""`` when the element doesn't resolve so callers can
    do ``if obs["value"]:``.
    """
    # Lazy import to keep this file mockable from tests that don't import gui.
    from case_sdk.harness.gui import _resolve_ax_element

    ref = _resolve_ax_element(element_ref)
    if ref is None:
        return ""
    try:
        val = getattr(ref, "AXValue", "")
    except Exception:
        return ""
    if val is None:
        return ""
    return str(val)


# ---- screenshot ----------------------------------------------------------


def _capture_screenshot(region: dict[str, Any] | None) -> dict[str, Any]:
    """Return a base64-encoded PNG of the screen (or a region).

    Enforces ``INLINE_BYTE_LIMIT``. If the encoded PNG exceeds the limit we
    skip base64 entirely and return ``{"value": "TOO_LARGE", "bytes": N}``.
    The blob-URL story for oversize payloads is a server-side concern.
    """
    if region is not None:
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        width = int(region.get("width", 0))
        height = int(region.get("height", 0))
        if width <= 0 or height <= 0:
            raise ValueError("screenshot region must have positive width/height")
        img = pyautogui.screenshot(region=(x, y, width, height))
    else:
        img = pyautogui.screenshot()

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    byte_len = len(png_bytes)

    if byte_len > INLINE_BYTE_LIMIT:
        return {"value": "TOO_LARGE", "bytes": byte_len}

    return {
        "png_b64": base64.b64encode(png_bytes).decode("ascii"),
        "bytes": byte_len,
    }
