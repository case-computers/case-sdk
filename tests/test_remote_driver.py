"""End-to-end tests for RemoteDriver against the mock FastAPI server.

These tests spin up a real subprocess via the `mock_server` context
manager, so they exercise the actual WebSocket transport rather than an
in-process loopback. The harness is faked so the system doesn't open
TextEdit during a unit test run.
"""

from __future__ import annotations

from tests._mock_server_fixture import fake_harness_recording, mock_server

from case_sdk.drivers.remote import RemoteDriver


def test_save_textedit_full_roundtrip():
    """One whole happy-path session: hello → 4 actions → done."""
    with mock_server() as srv:
        harness, calls = fake_harness_recording()
        driver = RemoteDriver(harness, api_base=str(srv["url"]), api_key="dev-key")

        result = driver.execute("save_textedit", {})

    assert result.get("success") is True

    # Server-side procedure: open_app → wait → type → hotkey.
    assert [c[0] for c in calls] == ["open_app", "wait", "type", "hotkey"]
    assert calls[0][1] == {"name": "TextEdit"}
    assert calls[1][1] == {"ms": 500}
    assert calls[2][1]["text"]  # default text from server
    assert calls[3][1] == {"combo": ["cmd", "s"]}


def test_remote_driver_passes_params():
    with mock_server() as srv:
        harness, calls = fake_harness_recording()
        driver = RemoteDriver(harness, api_base=str(srv["url"]), api_key="dev-key")

        driver.execute("save_textedit", {"text": "hello-from-test"})

    typed = next(c for c in calls if c[0] == "type")
    assert typed[1] == {"text": "hello-from-test"}


def test_remote_driver_strips_trailing_ws_path():
    """Customer pastes either ws://host or ws://host/ws — both should work."""
    with mock_server() as srv:
        harness, _ = fake_harness_recording()
        url_with_path = f"{srv['url']}/ws"
        driver = RemoteDriver(harness, api_base=url_with_path, api_key="dev-key")

        result = driver.execute("save_textedit", {})

    assert result.get("success") is True


def test_remote_driver_rejects_non_string_procedure():
    """The Driver ABC's first arg is a callable in LocalDriver, a name here.

    Passing a callable accidentally should fail loudly so customers see the
    right error rather than a JSON serialization mystery.
    """
    harness, _ = fake_harness_recording()
    driver = RemoteDriver(harness, api_base="ws://localhost:1", api_key="dev-key")

    def bogus(_params):
        yield None

    import pytest

    with pytest.raises(TypeError):
        driver.execute(bogus, {})
