"""Unit tests for the GUI action primitives.

We never call real pyautogui — every test mocks
``case_sdk.harness.gui.pyautogui`` so the mouse stays put. Tests assert that
each action dispatches the right call with the right kwargs and that the
return shape is what drivers expect (a dict).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_sdk.harness.executor import Harness, UnknownActionError
from case_sdk.harness.gui import build_default_harness


# ---- helpers -------------------------------------------------------------


def _patch_pyautogui():
    """Patch the module-level ``pyautogui`` symbol in ``case_sdk.harness.gui``.

    Returns the MagicMock so individual tests can assert on it. ``size`` is
    pre-stubbed so ``_clamp_coords`` has reasonable bounds.
    """
    patcher = patch("case_sdk.harness.gui.pyautogui")
    mock = patcher.start()
    mock.size.return_value = MagicMock(width=1920, height=1080)
    return patcher, mock


# ---- build_default_harness shape ----------------------------------------


def test_default_harness_registers_expected_actions():
    h = build_default_harness()
    assert isinstance(h, Harness)
    actions = set(h.known_actions())
    assert {
        "open_app",
        "activate_app",
        "click",
        "type",
        "hotkey",
        "scroll",
        "mouse_down",
        "mouse_up",
        "wait",
        "ax_press",
        "ax_set_value",
        "fn_key",
        "drag",
        "menu_click",
    }.issubset(actions)


def test_default_harness_registers_expected_sensors():
    h = build_default_harness()
    sensors = set(h.known_sensors())
    assert {"window_title", "ax_value", "screenshot"}.issubset(sensors)


def test_unknown_action_raises():
    h = build_default_harness()
    with pytest.raises(UnknownActionError):
        h.execute_action("doesnotexist", {})


# ---- open_app (P2 invariant) ---------------------------------------------


def test_open_app_invokes_subprocess():
    """``open_app`` must keep working after P3 — that's the P2 invariant."""
    with patch("case_sdk.harness.gui.subprocess.run") as run:
        h = build_default_harness()
        result = h.execute_action("open_app", {"name": "TextEdit"})
        run.assert_called_once_with(["open", "-a", "TextEdit"], check=True)
        assert result == {}


# ---- click ---------------------------------------------------------------


def test_click_dispatches_to_pyautogui():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        result = h.execute_action("click", {"x": 100, "y": 200})
        mock.click.assert_called_once_with(x=100, y=200, button="left", clicks=1)
        assert result == {"x": 100, "y": 200, "button": "left", "clicks": 1}
    finally:
        patcher.stop()


def test_click_passes_button_and_clicks():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        h.execute_action(
            "click", {"x": 10, "y": 20, "button": "right", "clicks": 2}
        )
        mock.click.assert_called_once_with(x=10, y=20, button="right", clicks=2)
    finally:
        patcher.stop()


def test_click_clamps_negative_coords():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        h.execute_action("click", {"x": -50, "y": -10})
        # _clamp_coords nudges <= 0 up to 1 to dodge pyautogui's fail-safe.
        kwargs = mock.click.call_args.kwargs
        assert kwargs["x"] >= 1
        assert kwargs["y"] >= 1
    finally:
        patcher.stop()


def test_click_clamps_overflow_coords():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        h.execute_action("click", {"x": 5000, "y": 5000})
        kwargs = mock.click.call_args.kwargs
        assert kwargs["x"] < 1920
        assert kwargs["y"] < 1080
    finally:
        patcher.stop()


# ---- type ----------------------------------------------------------------


def test_type_dispatches_typewrite():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        result = h.execute_action("type", {"text": "hello"})
        mock.typewrite.assert_called_once_with("hello", interval=0.0)
        assert result == {"chars": 5}
    finally:
        patcher.stop()


def test_type_passes_interval():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        h.execute_action("type", {"text": "abc", "interval": 0.05})
        mock.typewrite.assert_called_once_with("abc", interval=0.05)
    finally:
        patcher.stop()


# ---- hotkey --------------------------------------------------------------


def test_hotkey_unpacks_combo_list():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        result = h.execute_action("hotkey", {"combo": ["cmd", "s"]})
        mock.hotkey.assert_called_once_with("cmd", "s")
        assert result == {"combo": ["cmd", "s"]}
    finally:
        patcher.stop()


def test_hotkey_accepts_tuple_combo():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        h.execute_action("hotkey", {"combo": ("cmd", "shift", "p")})
        mock.hotkey.assert_called_once_with("cmd", "shift", "p")
    finally:
        patcher.stop()


def test_hotkey_empty_combo_raises():
    patcher, _ = _patch_pyautogui()
    try:
        h = build_default_harness()
        with pytest.raises(ValueError):
            h.execute_action("hotkey", {"combo": []})
    finally:
        patcher.stop()


# ---- scroll --------------------------------------------------------------


def test_scroll_without_coords_does_not_move_mouse():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        result = h.execute_action("scroll", {"clicks": 3})
        mock.scroll.assert_called_once_with(3)
        mock.moveTo.assert_not_called()
        assert result == {"clicks": 3}
    finally:
        patcher.stop()


def test_scroll_with_coords_moves_first():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        result = h.execute_action(
            "scroll", {"clicks": -2, "x": 100, "y": 200}
        )
        mock.moveTo.assert_called_once_with(100, 200, duration=0.0)
        mock.scroll.assert_called_once_with(-2, x=100, y=200)
        assert result == {"clicks": -2, "x": 100, "y": 200}
    finally:
        patcher.stop()


# ---- mouse_down / mouse_up ----------------------------------------------


def test_mouse_down_dispatches():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        result = h.execute_action("mouse_down", {"x": 50, "y": 60})
        mock.mouseDown.assert_called_once_with(x=50, y=60, button="left")
        assert result == {"x": 50, "y": 60, "button": "left"}
    finally:
        patcher.stop()


def test_mouse_up_dispatches_with_button():
    patcher, mock = _patch_pyautogui()
    try:
        h = build_default_harness()
        h.execute_action("mouse_up", {"x": 50, "y": 60, "button": "right"})
        mock.mouseUp.assert_called_once_with(x=50, y=60, button="right")
    finally:
        patcher.stop()


# ---- wait ----------------------------------------------------------------


def test_wait_converts_ms_to_seconds():
    with patch("case_sdk.harness.gui.time.sleep") as sleep:
        h = build_default_harness()
        result = h.execute_action("wait", {"ms": 250})
        sleep.assert_called_once_with(0.25)
        assert result == {"slept_ms": 250}


def test_wait_negative_clamped_to_zero():
    with patch("case_sdk.harness.gui.time.sleep") as sleep:
        h = build_default_harness()
        h.execute_action("wait", {"ms": -100})
        sleep.assert_called_once_with(0.0)


# ---- ax_press / ax_set_value --------------------------------------------


def test_ax_press_unresolved_returns_marker():
    h = build_default_harness()
    # Force the resolver to return None — we pass a non-dict to skip the
    # walk entirely.
    result = h.execute_action("ax_press", {"element_ref": "not-a-dict"})
    assert result == {"ok": False, "reason": "unresolved"}


def test_ax_set_value_unresolved_returns_marker():
    h = build_default_harness()
    result = h.execute_action(
        "ax_set_value", {"element_ref": "nope", "value": "x"}
    )
    assert result == {"ok": False, "reason": "unresolved"}


def test_ax_press_calls_press_when_resolved():
    fake = MagicMock()
    fake.Press = MagicMock()
    with patch(
        "case_sdk.harness.gui._resolve_ax_element", return_value=fake
    ):
        h = build_default_harness()
        result = h.execute_action(
            "ax_press", {"element_ref": {"role": "AXButton", "title": "OK"}}
        )
        fake.Press.assert_called_once()
        assert result == {"ok": True}


def test_ax_set_value_writes_axvalue_when_resolved():
    fake = MagicMock()
    with patch(
        "case_sdk.harness.gui._resolve_ax_element", return_value=fake
    ):
        h = build_default_harness()
        result = h.execute_action(
            "ax_set_value",
            {"element_ref": {"role": "AXTextField"}, "value": "hello"},
        )
        assert fake.AXValue == "hello"
        assert result == {"ok": True}


def test_ax_press_handles_press_failure():
    fake = MagicMock()
    fake.Press.side_effect = RuntimeError("boom")
    with patch(
        "case_sdk.harness.gui._resolve_ax_element", return_value=fake
    ):
        h = build_default_harness()
        result = h.execute_action(
            "ax_press", {"element_ref": {"role": "AXButton"}}
        )
        assert result["ok"] is False
        assert "press_failed" in result["reason"]
