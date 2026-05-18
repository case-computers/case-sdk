"""GUI harness — concrete implementations of action primitives.

P2 shipped one primitive: ``open_app``. P3 fills in the full pyautogui + AX set
that was ported from ``EmulatedHumanoid/backends/gui.py``. Everything LLM-
related (action schemas, element formatting, vision-server / `/state`, browser
CDP launch, ReAct find_element, prompt supplements) is intentionally NOT ported
— that lives server-side in ``case-api``.

All registered functions take only the kwargs that appear on the wire (see
``case_sdk.protocol``). Names of params must match the protocol exactly because
the executor invokes them via ``fn(**args)``.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

import pyautogui

from case_sdk.harness.executor import Harness


# ---- Coordinate helpers --------------------------------------------------


def _screen_size() -> tuple[int, int]:
    """Return the primary-display logical size in points.

    pyautogui already works in the same logical-point coordinate space the
    user clicks in, so we don't need an explicit retina scale factor. This
    helper exists so ``_clamp_coords`` can stay pure.
    """
    try:
        size = pyautogui.size()
        return int(size.width), int(size.height)
    except Exception:
        return 1920, 1080


def _clamp_coords(x: int, y: int) -> tuple[int, int]:
    """Clamp (x, y) into the primary-display bounds.

    pyautogui's fail-safe corner at (0, 0) raises ``FailSafeException`` so we
    nudge zero-or-negative coordinates to (1, 1) and pin the upper bound to
    one pixel inside the display edge.
    """
    w, h = _screen_size()
    cx = max(1, min(int(x), w - 1))
    cy = max(1, min(int(y), h - 1))
    return cx, cy


# ---- Harness builder -----------------------------------------------------


def build_default_harness() -> Harness:
    """Return a harness with the standard primitives + sensors registered.

    Backwards-compat: the signature stays parameterless because
    ``case_sdk.runtime.Runtime`` calls it with no args. New primitives are
    folded in here so callers automatically pick them up.
    """
    h = Harness()

    # ---- actions ----------------------------------------------------------

    @h.register_action("open_app")
    def open_app(name: str) -> dict[str, Any]:
        subprocess.run(["open", "-a", name], check=True)
        return {}

    @h.register_action("click")
    def click(
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        cx, cy = _clamp_coords(x, y)
        pyautogui.click(x=cx, y=cy, button=button, clicks=clicks)
        return {"x": cx, "y": cy, "button": button, "clicks": clicks}

    @h.register_action("type")
    def type_action(text: str, interval: float = 0.0) -> dict[str, Any]:
        # `type` shadows the builtin, hence the internal alias.
        pyautogui.typewrite(text, interval=interval)
        return {"chars": len(text)}

    @h.register_action("hotkey")
    def hotkey(combo: list[str]) -> dict[str, Any]:
        # combo is wire-side a list, but tolerate tuple for callers convenience.
        keys = list(combo)
        if not keys:
            raise ValueError("hotkey requires at least one key")
        pyautogui.hotkey(*keys)
        return {"combo": keys}

    @h.register_action("scroll")
    def scroll(
        clicks: int,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        if x is not None and y is not None:
            cx, cy = _clamp_coords(x, y)
            pyautogui.moveTo(cx, cy, duration=0.0)
            pyautogui.scroll(int(clicks), x=cx, y=cy)
            return {"clicks": int(clicks), "x": cx, "y": cy}
        pyautogui.scroll(int(clicks))
        return {"clicks": int(clicks)}

    @h.register_action("mouse_down")
    def mouse_down(x: int, y: int, button: str = "left") -> dict[str, Any]:
        cx, cy = _clamp_coords(x, y)
        pyautogui.mouseDown(x=cx, y=cy, button=button)
        return {"x": cx, "y": cy, "button": button}

    @h.register_action("mouse_up")
    def mouse_up(x: int, y: int, button: str = "left") -> dict[str, Any]:
        cx, cy = _clamp_coords(x, y)
        pyautogui.mouseUp(x=cx, y=cy, button=button)
        return {"x": cx, "y": cy, "button": button}

    @h.register_action("wait")
    def wait(ms: int) -> dict[str, Any]:
        # Clamp negative durations to 0 so a malformed yield can't hang us.
        seconds = max(0, int(ms)) / 1000.0
        time.sleep(seconds)
        return {"slept_ms": int(ms)}

    @h.register_action("ax_press")
    def ax_press(element_ref: dict[str, Any] | str) -> dict[str, Any]:
        """Press an AX element identified by ``element_ref``.

        v0 contract: ``element_ref`` is a dict describing how to locate the
        element on the frontmost app — currently supports::

            {"role": "AXButton", "title": "Save"}

        We walk the frontmost app's AX tree looking for the first match and
        call ``Press()``. If nothing resolves we return ``{"ok": False,
        "reason": "unresolved"}`` rather than raising — procedures should be
        able to branch on this.
        """
        ref = _resolve_ax_element(element_ref)
        if ref is None:
            return {"ok": False, "reason": "unresolved"}
        try:
            ref.Press()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "reason": f"press_failed: {exc}"}

    @h.register_action("ax_set_value")
    def ax_set_value(
        element_ref: dict[str, Any] | str, value: str
    ) -> dict[str, Any]:
        ref = _resolve_ax_element(element_ref)
        if ref is None:
            return {"ok": False, "reason": "unresolved"}
        try:
            ref.AXValue = value
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "reason": f"set_value_failed: {exc}"}

    # ---- sensors ----------------------------------------------------------
    # Wired here (not in a separate call) so consumers get one harness with
    # everything registered. Implementations live in ``sensors.py``.
    from case_sdk.harness import sensors as _sensors

    _sensors.register_default_sensors(h)

    return h


# ---- AX element resolution -----------------------------------------------


def _resolve_ax_element(element_ref: Any):
    """Best-effort resolution of an ``element_ref`` to an atomacos element.

    Accepts a dict like ``{"role": "AXButton", "title": "Save"}``. Returns the
    first match in the frontmost app's AX tree, or ``None`` if nothing matches
    or AX is unavailable. Designed so callers can use the truthiness of the
    return value as the success signal.
    """
    if not isinstance(element_ref, dict):
        return None
    role = element_ref.get("role")
    title = element_ref.get("title")
    value = element_ref.get("value")
    if not (role or title or value):
        return None
    try:
        import atomacos  # noqa: WPS433 — lazy to keep import cheap in tests
    except Exception:
        return None
    try:
        app = atomacos.getFrontmostApp()
    except Exception:
        return None
    return _walk_for_match(app, role=role, title=title, value=value, depth=0)


def _walk_for_match(elem, *, role, title, value, depth: int):
    if depth > 15:
        return None
    try:
        elem_role = getattr(elem, "AXRole", None)
    except Exception:
        elem_role = None
    if _ax_matches(elem_role, elem, role=role, title=title, value=value):
        return elem
    try:
        children = elem.AXChildren or []
    except Exception:
        children = []
    for child in children:
        match = _walk_for_match(
            child, role=role, title=title, value=value, depth=depth + 1
        )
        if match is not None:
            return match
    return None


def _ax_matches(elem_role, elem, *, role, title, value) -> bool:
    if role and elem_role != role:
        return False
    if title is not None:
        try:
            elem_title = getattr(elem, "AXTitle", None)
        except Exception:
            elem_title = None
        if elem_title != title:
            return False
    if value is not None:
        try:
            elem_value = getattr(elem, "AXValue", None)
        except Exception:
            elem_value = None
        if elem_value != value:
            return False
    # At least one of role/title/value must be non-None for a real match.
    return any(x is not None for x in (role, title, value))
