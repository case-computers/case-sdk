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


# macOS virtual keycodes for function keys (Carbon HIToolbox). Used by the
# ``fn_key`` action because pyautogui has no way to represent the Fn modifier.
_FN_KEYCODES: dict[str, int] = {
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "f13": 0x69, "f14": 0x6B, "f15": 0x71,
}


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

    @h.register_action("activate_app")
    def activate_app(name: str, settle_ms: int = 700) -> dict[str, Any]:
        """Bring an already-running app forward (osascript ``activate``)."""
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "{name}" to activate',
            ],
            check=False,
            capture_output=True,
        )
        time.sleep(max(0, int(settle_ms)) / 1000.0)
        return {"name": name, "settle_ms": int(settle_ms)}

    @h.register_action("menu_click")
    def menu_click(script: str, timeout: float = 5.0) -> dict[str, Any]:
        """Run an AppleScript snippet that clicks a menu item via System Events.

        Prefer this over hotkeys when macOS swallows function keys or focus is
        unreliable. The script should target ``process "App Name"`` menus.
        """
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
                timeout=max(0.5, float(timeout)),
            )
            return {"ok": True}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "reason": (e.stderr or "").strip() or str(e)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "osascript timed out"}

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

    @h.register_action("click_ref")
    def click_ref(element_ref: dict[str, Any] | str) -> dict[str, Any]:
        """Real mouse click at the centre of an AX element (pyautogui).

        Prefer over ``ax_press`` when AXPress silently no-ops (e.g. Resolve
        page-tab checkboxes). Resolves ``element_ref`` then clicks centre from
        AXPosition + AXSize.
        """
        ref = _resolve_ax_element(element_ref)
        if ref is None:
            return {"ok": False, "reason": "unresolved"}
        pos = _safe_ax_attr(ref, "AXPosition")
        size = _safe_ax_attr(ref, "AXSize")
        if pos is None or size is None:
            return {"ok": False, "reason": "no_position"}
        try:
            cx = int(pos.x) + int(size.width) // 2
            cy = int(pos.y) + int(size.height) // 2
        except Exception as exc:
            return {"ok": False, "reason": f"bad_bounds: {exc}"}
        cx, cy = _clamp_coords(cx, cy)
        pyautogui.click(x=cx, y=cy)
        return {"ok": True, "x": cx, "y": cy}

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

    @h.register_action("fn_key")
    def fn_key(key: str) -> dict[str, Any]:
        """Press a function key with the macOS Fn modifier held (Quartz CGEvent)."""
        keycode = _FN_KEYCODES.get(key.lower())
        if keycode is None:
            raise ValueError(
                f"fn_key: unknown key {key!r}; expected one of "
                f"{sorted(_FN_KEYCODES)}"
            )
        try:
            from Quartz import (
                CGEventCreateKeyboardEvent,
                CGEventPost,
                CGEventSetFlags,
                kCGEventFlagMaskSecondaryFn,
                kCGHIDEventTap,
            )
        except Exception as exc:  # pragma: no cover — macOS-only path
            raise RuntimeError(
                "fn_key requires pyobjc-framework-Quartz on macOS"
            ) from exc
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        CGEventSetFlags(down, kCGEventFlagMaskSecondaryFn)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.02)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        CGEventSetFlags(up, kCGEventFlagMaskSecondaryFn)
        CGEventPost(kCGHIDEventTap, up)
        return {"key": key, "fn": True}

    @h.register_action("drag")
    def drag(
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int = 500,
        button: str = "left",
    ) -> dict[str, Any]:
        cx1, cy1 = _clamp_coords(start_x, start_y)
        cx2, cy2 = _clamp_coords(end_x, end_y)
        duration_s = max(0, int(duration_ms)) / 1000.0
        pyautogui.moveTo(cx1, cy1, duration=0.0)
        pyautogui.dragTo(cx2, cy2, duration=duration_s, button=button)
        return {
            "start": [cx1, cy1],
            "end": [cx2, cy2],
            "duration_ms": int(duration_ms),
        }

    # ---- sensors ----------------------------------------------------------
    # Wired here (not in a separate call) so consumers get one harness with
    # everything registered. Implementations live in ``sensors.py``.
    from case_sdk.harness import sensors as _sensors

    _sensors.register_default_sensors(h)

    return h


# ---- AX element resolution -----------------------------------------------


def _safe_ax_attr(elem: Any, name: str) -> Any:
    try:
        return getattr(elem, name, None)
    except Exception:
        return None


def _resolve_ax_element(element_ref: Any):
    """Resolve ``element_ref`` via :mod:`case_sdk.inspect`."""
    from case_sdk.inspect import resolve_element_ref

    return resolve_element_ref(element_ref)
