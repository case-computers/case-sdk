"""Unit tests for the sensor primitives.

Every sensor that touches the OS (NSWorkspace, atomacos, pyautogui.screenshot)
is mocked. The 8 KB inline limit is exercised both above and below threshold.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import pytest

from case_sdk.harness import sensors as sensors_mod
from case_sdk.harness.executor import Harness, UnknownSensorError
from case_sdk.harness.gui import build_default_harness


# ---- registration --------------------------------------------------------


def test_register_default_sensors_adds_all_three():
    h = Harness()
    sensors_mod.register_default_sensors(h)
    assert set(h.known_sensors()) == {"window_title", "ax_value", "screenshot"}


def test_unknown_sensor_raises():
    h = build_default_harness()
    with pytest.raises(UnknownSensorError):
        h.execute_sensor("doesnotexist", {})


# ---- window_title --------------------------------------------------------


def test_window_title_returns_app_name_only_when_no_ax_title():
    app = MagicMock()
    app.localizedName.return_value = "TextEdit"
    workspace = MagicMock()
    workspace.frontmostApplication.return_value = app
    fake_ns = MagicMock()
    fake_ns.sharedWorkspace.return_value = workspace

    appkit_mod = MagicMock(NSWorkspace=fake_ns)
    with patch.dict("sys.modules", {"AppKit": appkit_mod}):
        with patch.object(
            sensors_mod, "_frontmost_window_title", return_value=None
        ):
            h = build_default_harness()
            result = h.execute_sensor("window_title", {})
            assert result == {"value": "TextEdit"}


def test_window_title_combines_app_and_ax_title():
    app = MagicMock()
    app.localizedName.return_value = "TextEdit"
    workspace = MagicMock()
    workspace.frontmostApplication.return_value = app
    fake_ns = MagicMock()
    fake_ns.sharedWorkspace.return_value = workspace

    appkit_mod = MagicMock(NSWorkspace=fake_ns)
    with patch.dict("sys.modules", {"AppKit": appkit_mod}):
        with patch.object(
            sensors_mod, "_frontmost_window_title", return_value="Untitled"
        ):
            h = build_default_harness()
            result = h.execute_sensor("window_title", {})
            assert result == {"value": "TextEdit — Untitled"}


def test_window_title_blacklisted_app_returns_empty():
    app = MagicMock()
    app.localizedName.return_value = "Spotlight"
    workspace = MagicMock()
    workspace.frontmostApplication.return_value = app
    fake_ns = MagicMock()
    fake_ns.sharedWorkspace.return_value = workspace

    appkit_mod = MagicMock(NSWorkspace=fake_ns)
    with patch.dict("sys.modules", {"AppKit": appkit_mod}):
        h = build_default_harness()
        result = h.execute_sensor("window_title", {})
        assert result == {"value": ""}


def test_window_title_no_frontmost_returns_empty():
    workspace = MagicMock()
    workspace.frontmostApplication.return_value = None
    fake_ns = MagicMock()
    fake_ns.sharedWorkspace.return_value = workspace

    appkit_mod = MagicMock(NSWorkspace=fake_ns)
    with patch.dict("sys.modules", {"AppKit": appkit_mod}):
        h = build_default_harness()
        result = h.execute_sensor("window_title", {})
        assert result == {"value": ""}


def test_window_title_swallows_nsworkspace_errors():
    workspace = MagicMock()
    workspace.frontmostApplication.side_effect = RuntimeError("nope")
    fake_ns = MagicMock()
    fake_ns.sharedWorkspace.return_value = workspace

    appkit_mod = MagicMock(NSWorkspace=fake_ns)
    with patch.dict("sys.modules", {"AppKit": appkit_mod}):
        h = build_default_harness()
        result = h.execute_sensor("window_title", {})
        assert result == {"value": ""}


# ---- ax_value (v0 stub) --------------------------------------------------


def test_ax_value_unresolved_returns_empty_string():
    h = build_default_harness()
    # Non-dict element_ref makes the resolver bail immediately.
    result = h.execute_sensor("ax_value", {"element_ref": "not-a-dict"})
    assert result == {"value": ""}


def test_ax_value_no_element_ref_returns_empty_string():
    h = build_default_harness()
    result = h.execute_sensor("ax_value", {})
    assert result == {"value": ""}


def test_ax_value_reads_resolved_element_axvalue():
    fake = MagicMock()
    fake.AXValue = "hello world"
    with patch(
        "case_sdk.harness.gui._resolve_ax_element", return_value=fake
    ):
        h = build_default_harness()
        result = h.execute_sensor(
            "ax_value", {"element_ref": {"role": "AXTextField"}}
        )
        assert result == {"value": "hello world"}


def test_ax_value_handles_attribute_error_on_axvalue():
    """A resolved element with no AXValue should not crash the sensor."""
    fake = MagicMock(spec=["AXTitle"])  # no AXValue attribute on the spec
    with patch(
        "case_sdk.harness.gui._resolve_ax_element", return_value=fake
    ):
        h = build_default_harness()
        result = h.execute_sensor(
            "ax_value", {"element_ref": {"role": "AXTextField"}}
        )
        # MagicMock(spec=[...]) raises AttributeError for unlisted attrs
        # -> the sensor's getattr default ("") kicks in and we get "".
        assert result == {"value": ""}


# ---- screenshot ----------------------------------------------------------


def _fake_png_image(num_bytes: int):
    """Return a MagicMock that mimics the PIL Image returned by pyautogui.

    Calling ``.save(buf, format="PNG")`` writes ``num_bytes`` of placeholder
    bytes to the buffer so we can exercise the size-check path without
    actually encoding a PNG.
    """
    image = MagicMock()

    def _save(buf, format=None):  # noqa: ARG001 — match PIL.Image.save signature
        buf.write(b"x" * num_bytes)

    image.save.side_effect = _save
    return image


def test_screenshot_returns_b64_under_limit():
    fake_img = _fake_png_image(num_bytes=512)
    with patch.object(sensors_mod.pyautogui, "screenshot", return_value=fake_img):
        h = build_default_harness()
        result = h.execute_sensor("screenshot", {})
        assert result["bytes"] == 512
        assert "png_b64" in result
        # decode to confirm the b64 is valid
        decoded = base64.b64decode(result["png_b64"])
        assert decoded == b"x" * 512


def test_screenshot_over_limit_returns_marker():
    too_big = sensors_mod.INLINE_BYTE_LIMIT + 1
    fake_img = _fake_png_image(num_bytes=too_big)
    with patch.object(sensors_mod.pyautogui, "screenshot", return_value=fake_img):
        h = build_default_harness()
        result = h.execute_sensor("screenshot", {})
        assert result == {"value": "TOO_LARGE", "bytes": too_big}


def test_screenshot_at_exactly_limit_is_inline():
    """Boundary: equal to limit is OK; only strictly greater triggers TOO_LARGE."""
    at_limit = sensors_mod.INLINE_BYTE_LIMIT
    fake_img = _fake_png_image(num_bytes=at_limit)
    with patch.object(sensors_mod.pyautogui, "screenshot", return_value=fake_img):
        h = build_default_harness()
        result = h.execute_sensor("screenshot", {})
        assert "png_b64" in result
        assert result["bytes"] == at_limit


def test_screenshot_with_region_crops_to_tuple():
    fake_img = _fake_png_image(num_bytes=100)
    with patch.object(
        sensors_mod.pyautogui, "screenshot", return_value=fake_img
    ) as shot:
        h = build_default_harness()
        h.execute_sensor(
            "screenshot",
            {"region": {"x": 10, "y": 20, "width": 30, "height": 40}},
        )
        shot.assert_called_once_with(region=(10, 20, 30, 40))


def test_screenshot_rejects_zero_dimension_region():
    h = build_default_harness()
    with pytest.raises(ValueError):
        h.execute_sensor(
            "screenshot",
            {"region": {"x": 0, "y": 0, "width": 0, "height": 100}},
        )
