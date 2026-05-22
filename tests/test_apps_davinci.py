"""Unit tests for the ``case_sdk.apps.davinci`` action bundle.

Mocks ``pyautogui`` and the AX walking helpers in ``_ax`` so tests run on
any machine (no DaVinci required). Asserts each action dispatches the
expected keyboard / mouse sequence and that registration is wired into
``build_default_harness`` correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_sdk.harness.executor import Harness


# ---- registration shape -------------------------------------------------


def test_davinci_actions_register_into_default_harness():
    """``build_default_harness`` attaches all five davinci_* actions on macOS."""
    from case_sdk.harness.gui import build_default_harness

    h = build_default_harness()
    davinci = sorted(a for a in h.known_actions() if a.startswith("davinci_"))
    assert davinci == [
        "davinci_color_wheel_reset",
        "davinci_select_pool_clip",
        "davinci_set_inspector_combo",
        "davinci_set_label_numeric",
        "davinci_set_rich_text",
    ]


def test_davinci_register_raises_without_pyautogui():
    """If pyautogui is None at module level, register raises ImportError."""
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", None):
        with pytest.raises(ImportError, match="pyautogui"):
            actions.register(h)


# ---- davinci_set_label_numeric ------------------------------------------


def _fake_label_element(value: str) -> MagicMock:
    el = MagicMock()
    el.AXValue = value
    el.AXPosition = MagicMock(x=400.0, y=300.0)
    el.AXSize = MagicMock(width=60.0, height=20.0)
    return el


def test_set_label_numeric_double_clicks_and_types():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()) as mock_pg, \
            patch.object(actions, "_ax") as mock_ax, \
            patch.object(actions, "_focus_resolve"):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        numeric_el = _fake_label_element("0.50")
        mock_ax.find_numeric_neighbor.return_value = numeric_el
        mock_ax._ax_point.side_effect = lambda e: (e.AXPosition.x, e.AXPosition.y)
        mock_ax._safe_attr.side_effect = lambda e, n: getattr(e, n, None)
        actions.register(h)

        result = h.execute_action(
            "davinci_set_label_numeric",
            {"label": "Temp", "value": "5500", "verify_readback": False},
        )

    mock_pg.doubleClick.assert_called_once()
    mock_pg.hotkey.assert_any_call("command", "a")
    mock_pg.typewrite.assert_called_once_with("5500", interval=0.01)
    mock_pg.press.assert_any_call("return")
    assert result["label"] == "Temp"
    assert result["value"] == "5500"


def test_set_label_numeric_readback_mismatch_raises():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()) as mock_pg, \
            patch.object(actions, "_ax") as mock_ax, \
            patch.object(actions, "_focus_resolve"):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        first = _fake_label_element("0.00")
        second = _fake_label_element("99.99")  # readback diverges from "5500"
        mock_ax.find_numeric_neighbor.side_effect = [first, second]
        mock_ax._ax_point.side_effect = lambda e: (e.AXPosition.x, e.AXPosition.y)
        mock_ax._safe_attr.side_effect = lambda e, n: getattr(e, n, None)
        actions.register(h)

        with pytest.raises(Exception, match="readback mismatch"):
            h.execute_action(
                "davinci_set_label_numeric",
                {"label": "Temp", "value": "5500", "verify_readback": True},
            )


# ---- davinci_set_rich_text ----------------------------------------------


def test_set_rich_text_clears_then_types():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()) as mock_pg, \
            patch.object(actions, "_ax") as mock_ax, \
            patch.object(actions, "_focus_resolve"):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        text_area = MagicMock()
        app_root = MagicMock()
        mock_ax._get_resolve_app.return_value = app_root
        # AXRole map keyed by object identity — MagicMock auto-attrs would
        # confuse the comparison otherwise.
        roles = {id(app_root): "AXApplication", id(text_area): "AXTextArea"}
        mock_ax._safe_attr.side_effect = (
            lambda e, n: roles.get(id(e)) if n == "AXRole" else None
        )
        children_map: dict[int, list] = {
            id(app_root): [text_area],
            id(text_area): [],
        }
        mock_ax._safe_children.side_effect = lambda e: children_map.get(id(e), [])
        mock_ax._ax_point.return_value = (500.0, 400.0)
        actions.register(h)

        result = h.execute_action("davinci_set_rich_text", {"text": "Hello"})

    # Two cmd+a + backspace passes
    cmd_a_calls = [c for c in mock_pg.hotkey.call_args_list if c.args == ("command", "a")]
    assert len(cmd_a_calls) >= 2
    # Backspace + delete cascade + option-backspace cascade all fired
    backspace_calls = [c for c in mock_pg.press.call_args_list if c.args == ("backspace",)]
    delete_calls = [c for c in mock_pg.press.call_args_list if c.args == ("delete",)]
    opt_back = [c for c in mock_pg.hotkey.call_args_list if c.args == ("option", "backspace")]
    assert len(backspace_calls) >= 24
    assert len(delete_calls) >= 24
    assert len(opt_back) >= 24
    mock_pg.write.assert_called_once_with("Hello", interval=0.012)
    assert result["text"] == "Hello"


# ---- davinci_select_pool_clip -------------------------------------------


def test_select_pool_clip_types_stem_and_selects():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()) as mock_pg, \
            patch.object(actions, "_ax") as mock_ax, \
            patch.object(actions, "_focus_resolve"):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        field = MagicMock()
        mock_ax.find_media_pool_search.return_value = field
        mock_ax._ax_point.return_value = (300.0, 200.0)
        actions.register(h)

        result = h.execute_action(
            "davinci_select_pool_clip",
            {"name": "my_clip.mov"},
        )

    mock_pg.click.assert_called_once()
    mock_pg.typewrite.assert_called_once_with("my_clip", interval=0.01)
    mock_pg.press.assert_any_call("down")
    mock_pg.press.assert_any_call("return")
    assert result["stem"] == "my_clip"


def test_select_pool_clip_raises_when_field_missing():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()), \
            patch.object(actions, "_ax") as mock_ax:
        mock_ax.find_media_pool_search.return_value = None
        actions.register(h)
        with pytest.raises(Exception, match="search field not found"):
            h.execute_action("davinci_select_pool_clip", {"name": "foo.mov"})


# ---- davinci_set_inspector_combo ----------------------------------------


def test_set_inspector_combo_dispatches():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()) as mock_pg, \
            patch.object(actions, "_ax") as mock_ax, \
            patch.object(actions, "_focus_resolve"):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        combo = MagicMock()
        mock_ax.find_combo_box.return_value = combo
        mock_ax._ax_point.return_value = (800.0, 500.0)
        actions.register(h)

        result = h.execute_action(
            "davinci_set_inspector_combo",
            {"combo": "Font Family", "value": "Helvetica"},
        )

    mock_pg.typewrite.assert_called_once_with("Helvetica", interval=0.012)
    mock_pg.press.assert_any_call("return")
    assert result["combo"] == "Font Family"
    assert result["value"] == "Helvetica"


# ---- davinci_color_wheel_reset ------------------------------------------


def test_color_wheel_reset_clicks_correct_index():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()) as mock_pg, \
            patch.object(actions, "_ax") as mock_ax, \
            patch.object(actions, "_focus_resolve"):
        mock_pg.size.return_value = MagicMock(width=1920, height=1080)
        # 4 fake buttons sorted left-to-right (lift=0..offset=3).
        buttons = [MagicMock(name=f"reset_{i}") for i in range(4)]
        mock_ax.find_wheel_reset_buttons.return_value = buttons
        # Each button reports a distinct position so we can verify index.
        positions = {id(buttons[i]): (100.0 + i * 200, 600.0) for i in range(4)}
        mock_ax._ax_point.side_effect = lambda e: positions[id(e)]
        actions.register(h)

        result = h.execute_action("davinci_color_wheel_reset", {"wheel": "gain"})

    # gain → index 2 → x ~ 500
    args, _kwargs = mock_pg.click.call_args
    assert args[0] == 500
    assert result == {"wheel": "gain", "index": 2}


def test_color_wheel_reset_unknown_wheel_raises():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()), \
            patch.object(actions, "_ax"):
        actions.register(h)
        with pytest.raises(ValueError, match="Unknown wheel"):
            h.execute_action("davinci_color_wheel_reset", {"wheel": "bogus"})


def test_color_wheel_reset_when_buttons_missing():
    from case_sdk.apps.davinci import actions

    h = Harness()
    with patch.object(actions, "pyautogui", MagicMock()), \
            patch.object(actions, "_ax") as mock_ax:
        mock_ax.find_wheel_reset_buttons.return_value = []
        actions.register(h)
        with pytest.raises(Exception, match="Could not locate"):
            h.execute_action("davinci_color_wheel_reset", {"wheel": "lift"})
