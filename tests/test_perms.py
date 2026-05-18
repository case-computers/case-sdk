"""Tests for case_sdk.perms.

Detection is mocked everywhere — we never actually probe the host's TCC
database. Both `AXIsProcessTrusted` and `screenshot` are imported at module
level in `case_sdk.perms`, so we monkeypatch those attributes directly.
"""

from __future__ import annotations

import pytest

from case_sdk import perms
from case_sdk.perms import (
    ACCESSIBILITY,
    SCREEN_RECORDING,
    PermStatus,
    check_perms,
    ensure_perms,
    missing,
    print_onboarding_banner,
)


class _FakeImage:
    """Stand-in for a PIL.Image with a fixed extrema."""

    def __init__(self, extrema):
        self._extrema = extrema

    def getextrema(self):
        return self._extrema


def _patch_granted(monkeypatch, *, ax: bool, screen_extrema):
    monkeypatch.setattr(perms, "AXIsProcessTrusted", lambda: ax)

    def _fake_screenshot(*args, **kwargs):
        return _FakeImage(screen_extrema)

    monkeypatch.setattr(perms, "screenshot", _fake_screenshot)


# Standard RGB extrema for a non-black 1x1 capture.
NON_BLACK_EXTREMA = ((128, 128), (64, 64), (200, 200))
# All-black RGB extrema (what macOS substitutes when Screen Recording is denied).
BLACK_EXTREMA = ((0, 0), (0, 0), (0, 0))


def test_check_perms_all_granted(monkeypatch):
    _patch_granted(monkeypatch, ax=True, screen_extrema=NON_BLACK_EXTREMA)
    status = check_perms()
    assert status.accessibility is True
    assert status.screen_recording is True
    # Input Monitoring is undetectable — must be None, never True/False.
    assert status.input_monitoring is None


def test_check_perms_accessibility_missing(monkeypatch):
    _patch_granted(monkeypatch, ax=False, screen_extrema=NON_BLACK_EXTREMA)
    status = check_perms()
    assert status.accessibility is False
    assert status.screen_recording is True
    assert status.input_monitoring is None


def test_check_perms_screen_recording_black(monkeypatch):
    """All-zero pixels → macOS substituted them → perm denied."""
    _patch_granted(monkeypatch, ax=True, screen_extrema=BLACK_EXTREMA)
    status = check_perms()
    assert status.accessibility is True
    assert status.screen_recording is False
    assert status.input_monitoring is None


def test_check_perms_screen_recording_raises(monkeypatch):
    """If screenshot raises, treat as missing rather than crashing."""
    monkeypatch.setattr(perms, "AXIsProcessTrusted", lambda: True)

    def _boom(*args, **kwargs):
        raise RuntimeError("pyscreeze unavailable")

    monkeypatch.setattr(perms, "screenshot", _boom)
    status = check_perms()
    assert status.screen_recording is False


def test_missing_returns_correct_list():
    status = PermStatus(
        accessibility=False,
        screen_recording=False,
        input_monitoring=None,
    )
    assert missing(status) == [ACCESSIBILITY, SCREEN_RECORDING]


def test_missing_skips_undetectable():
    """input_monitoring=None must NOT show up in the missing list — we don't
    block on perms we can't observe."""
    status = PermStatus(
        accessibility=True,
        screen_recording=True,
        input_monitoring=None,
    )
    assert missing(status) == []


def test_missing_single_perm():
    status = PermStatus(
        accessibility=False,
        screen_recording=True,
        input_monitoring=None,
    )
    assert missing(status) == [ACCESSIBILITY]


def test_print_banner_smoke(capsys):
    # Empty list → completely silent.
    print_onboarding_banner([])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""

    # Single perm → writes to stderr, mentions just that perm, not the other.
    print_onboarding_banner([ACCESSIBILITY])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Accessibility" in captured.err
    assert "Screen Recording" not in captured.err
    # Should include the deep-link and a tccutil reset line.
    assert "x-apple.systempreferences" in captured.err
    assert "tccutil reset" in captured.err


def test_print_banner_uses_terminal_bundle_id(monkeypatch, capsys):
    monkeypatch.setenv("__CFBundleIdentifier", "com.googlecode.iterm2")
    print_onboarding_banner([ACCESSIBILITY])
    err = capsys.readouterr().err
    assert "com.googlecode.iterm2" in err


def test_print_banner_falls_back_when_no_bundle_id(monkeypatch, capsys):
    monkeypatch.delenv("__CFBundleIdentifier", raising=False)
    print_onboarding_banner([SCREEN_RECORDING])
    err = capsys.readouterr().err
    assert "com.apple.Terminal" in err


def test_ensure_perms_exits_if_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        perms,
        "check_perms",
        lambda: PermStatus(
            accessibility=False,
            screen_recording=True,
            input_monitoring=None,
        ),
    )
    with pytest.raises(SystemExit) as exc:
        ensure_perms()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Accessibility" in err


def test_ensure_perms_returns_when_all_good(monkeypatch, capsys):
    monkeypatch.setattr(
        perms,
        "check_perms",
        lambda: PermStatus(
            accessibility=True,
            screen_recording=True,
            input_monitoring=None,
        ),
    )
    # Must NOT raise, must NOT print anything.
    ensure_perms()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
