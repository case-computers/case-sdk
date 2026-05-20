"""Unit tests for case_sdk.inspect (mocked atomacos / AppKit)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_sdk.inspect import (
    InspectError,
    app_tree,
    find_elements,
    resolve_element_ref,
)


def _mock_elem(
    *,
    role="AXButton",
    title="Save",
    children=None,
    value=None,
):
    elem = MagicMock()
    elem.AXRole = role
    elem.AXTitle = title
    elem.AXSubrole = None
    elem.AXDescription = None
    elem.AXValue = value
    elem.AXEnabled = True
    elem.AXPosition = None
    elem.AXSize = None
    elem.AXChildren = children or []
    return elem


@patch("case_sdk.inspect._get_app_element")
def test_app_tree_serializes_children(mock_get):
    child = _mock_elem(role="AXButton", title="OK", children=[])
    root = _mock_elem(role="AXWindow", title="Win", children=[child])
    mock_get.return_value = (root, {"app": "TestApp", "pid": 42})

    out = app_tree("TestApp", max_depth=4, max_children=10, max_nodes=100)
    assert out["app"] == "TestApp"
    assert out["tree"]["role"] == "AXWindow"
    assert out["tree"]["children"][0]["title"] == "OK"


@patch("case_sdk.inspect._get_app_element")
def test_find_elements_returns_refs(mock_get):
    child = _mock_elem(role="AXButton", title="Append", children=[])
    root = _mock_elem(role="AXApplication", title="DaVinci", children=[child])
    mock_get.return_value = (root, {"app": "DaVinci Resolve", "pid": 1})

    hits = find_elements(
        "DaVinci",
        role="AXButton",
        title_contains="Append",
        max_results=5,
    )
    assert len(hits) == 1
    assert hits[0]["ref"]["role"] == "AXButton"


@patch("case_sdk.inspect._get_app_element")
def test_resolve_element_ref_by_role_title(mock_get):
    target = _mock_elem(role="AXButton", title="Save")
    root = _mock_elem(role="AXWindow", title="W", children=[target])
    mock_get.return_value = (root, {"app": "App", "pid": 1})

    resolved = resolve_element_ref({"role": "AXButton", "title": "Save"})
    assert resolved is target


def test_resolve_element_ref_invalid_returns_none():
    assert resolve_element_ref(None) is None
    assert resolve_element_ref("not-a-dict") is None


@patch("case_sdk.inspect._find_pid_by_name", return_value=None)
def test_get_app_element_no_match_raises(_mock_pid):
    with pytest.raises(InspectError, match="no running app"):
        app_tree("NonexistentAppXYZ")


def _running_app(*, name: str, bundle: str | None = None, pid: int = 0):
    """Build a minimal mock of NSRunningApplication."""
    app = MagicMock()
    app.localizedName.return_value = name
    app.bundleIdentifier.return_value = bundle
    app.processIdentifier.return_value = pid
    return app


@patch("AppKit.NSWorkspace")
def test_find_pid_prefers_exact_localized_name_over_overlay(mock_ns_workspace):
    """Regression: case-sdk ax-tree TextEdit must not resolve to QuickLookUIService."""
    from case_sdk.inspect import _find_pid_by_name

    overlay = _running_app(
        name="QuickLookUIService",
        bundle="com.apple.quicklook.ui.helper",
        pid=11,
    )
    target = _running_app(
        name="TextEdit",
        bundle="com.apple.TextEdit",
        pid=22,
    )
    workspace = MagicMock()
    workspace.runningApplications.return_value = [overlay, target]
    mock_ns_workspace.sharedWorkspace.return_value = workspace

    assert _find_pid_by_name("TextEdit") == 22


@patch("AppKit.NSWorkspace")
def test_find_pid_resolves_by_bundle_id(mock_ns_workspace):
    from case_sdk.inspect import _find_pid_by_name

    other = _running_app(name="Other", bundle="com.example.other", pid=10)
    target = _running_app(name="TextEdit", bundle="com.apple.TextEdit", pid=22)
    workspace = MagicMock()
    workspace.runningApplications.return_value = [other, target]
    mock_ns_workspace.sharedWorkspace.return_value = workspace

    # Exact bundle id.
    assert _find_pid_by_name("com.apple.TextEdit") == 22
    # com.apple.<name> shortcut.
    workspace.runningApplications.return_value = [other, target]
    assert _find_pid_by_name("TextEdit") == 22


@patch("AppKit.NSWorkspace")
def test_find_pid_skips_helper_services_on_substring_fallback(mock_ns_workspace):
    from case_sdk.inspect import _find_pid_by_name

    overlay = _running_app(
        name="QuickLookUIService",
        bundle="com.apple.quicklook.ui.helper",
        pid=11,
    )
    workspace = MagicMock()
    workspace.runningApplications.return_value = [overlay]
    mock_ns_workspace.sharedWorkspace.return_value = workspace

    # Only the helper is running and the substring is contained in the
    # helper name — fallback should still skip helpers and return None.
    assert _find_pid_by_name("Service") is None
