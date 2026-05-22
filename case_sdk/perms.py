"""macOS permissions detection + onboarding banner.

Accessibility is checked via ``AXIsProcessTrusted``; Screen Recording is
inferred from a 1x1 screenshot (macOS swaps in black pixels when denied);
Input Monitoring has no reliable probe and is reported as undetectable.
Missing native deps degrade to "missing" rather than crashing.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Detection helpers are imported at module level so tests can monkeypatch
# ``case_sdk.perms.AXIsProcessTrusted`` and ``case_sdk.perms.screenshot``
# directly. If the native bindings can't import (CI, non-macOS), fall back to
# stubs that report missing rather than blowing up at import time.
try:
    from atomacos._a11y import AXIsProcessTrusted  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fallback for non-mac / broken installs
    try:
        from HIServices import AXIsProcessTrusted  # type: ignore[import-not-found]
    except Exception:

        def AXIsProcessTrusted() -> bool:  # type: ignore[no-redef]
            return False


try:
    from pyautogui import screenshot  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fallback if pyautogui can't import

    def screenshot(*args, **kwargs):  # type: ignore[no-redef]
        raise RuntimeError("pyautogui.screenshot unavailable")


# Human-readable permission names. These are what `missing()` returns and what
# `print_onboarding_banner` keys off — keep them consistent with the banner.
ACCESSIBILITY = "Accessibility"
SCREEN_RECORDING = "Screen Recording"
INPUT_MONITORING = "Input Monitoring"


@dataclass
class PermStatus:
    accessibility: bool
    screen_recording: bool
    input_monitoring: bool | None  # None if undetectable


def _accessibility_granted() -> bool:
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def _screen_recording_granted() -> bool:
    """Heuristic: grab a 1x1 screenshot. macOS replaces denied captures with
    all-black pixels. If the call raises or the pixel is all zero across every
    channel, we treat Screen Recording as not granted."""
    try:
        img = screenshot(region=(0, 0, 1, 1))
    except Exception:
        return False
    try:
        extrema = img.getextrema()
    except Exception:
        return False
    # `getextrema` returns either ((lo, hi),) for L mode or a tuple of (lo, hi)
    # per channel for RGB/RGBA. Some PIL versions return a single (lo, hi) for
    # 1-pixel L images. Normalise to a list of (lo, hi) tuples and check that
    # at least one channel is non-zero.
    if not isinstance(extrema, tuple):
        return True  # unexpected shape — assume granted rather than scare
    if len(extrema) == 2 and all(isinstance(v, int) for v in extrema):
        extrema = (extrema,)  # type: ignore[assignment]
    for channel in extrema:
        try:
            lo, hi = channel
        except Exception:
            return True
        if hi != 0:
            return True
    return False


def check_perms() -> PermStatus:
    """Probe each permission and return a snapshot.

    Accessibility is detected directly. Screen Recording is detected by the
    black-pixel heuristic. Input Monitoring is not directly probeable from
    Python on modern macOS so it is reported as ``None`` (undetectable)."""
    return PermStatus(
        accessibility=_accessibility_granted(),
        screen_recording=_screen_recording_granted(),
        input_monitoring=None,
    )


def missing(status: PermStatus) -> list[str]:
    """Return human-readable names of perms that are definitely missing.

    Perms with ``None`` status (undetectable, e.g. Input Monitoring) are NOT
    included — we never block on something we can't observe."""
    out: list[str] = []
    if status.accessibility is False:
        out.append(ACCESSIBILITY)
    if status.screen_recording is False:
        out.append(SCREEN_RECORDING)
    return out


# Banner description text per perm.
_DESCRIPTIONS: dict[str, str] = {
    ACCESSIBILITY: "control mouse, keyboard, AX tree",
    SCREEN_RECORDING: "capture window state for sensors",
}

# Deep-link URL per perm.
_DEEP_LINKS: dict[str, str] = {
    ACCESSIBILITY: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ),
    SCREEN_RECORDING: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
    ),
}

# `tccutil reset` category name per perm.
_TCC_CATEGORIES: dict[str, str] = {
    ACCESSIBILITY: "Accessibility",
    SCREEN_RECORDING: "ScreenCapture",
}


def _terminal_bundle_id() -> str:
    return os.environ.get("__CFBundleIdentifier", "com.apple.Terminal")


def print_onboarding_banner(missing_perms: list[str]) -> None:
    """Print the macOS onboarding banner to stderr.

    Called with an empty list, it returns silently — `ensure_perms` only calls
    it when there's something to say. Banner lists only the perms in
    ``missing_perms`` so the message stays honest when only one is missing."""
    if not missing_perms:
        return

    bundle_id = _terminal_bundle_id()

    lines: list[str] = []
    lines.append("⚠️  case-sdk needs macOS permissions")
    lines.append("")
    lines.append("Missing:")
    for name in missing_perms:
        desc = _DESCRIPTIONS.get(name, "")
        if desc:
            lines.append(f"  • {name} — {desc}")
        else:
            lines.append(f"  • {name}")
    lines.append("")
    lines.append("Grant in System Settings → Privacy & Security:")
    # Pad the label column so the open commands align even when only one perm
    # is missing.
    max_label = max(len(f"{name}:") for name in missing_perms)
    for name in missing_perms:
        link = _DEEP_LINKS.get(name)
        if not link:
            continue
        label = f"{name}:".ljust(max_label + 2)
        lines.append(f"  {label}open \"{link}\"")
    lines.append("")
    lines.append("If you previously denied, reset with:")
    max_cat = max(
        len(_TCC_CATEGORIES.get(name, name)) for name in missing_perms
    )
    for name in missing_perms:
        category = _TCC_CATEGORIES.get(name)
        if not category:
            continue
        lines.append(
            f"  tccutil reset {category.ljust(max_cat)}    {bundle_id}"
        )
    lines.append("")
    lines.append("Then re-run case-sdk.")

    print("\n".join(lines), file=sys.stderr)


def ensure_perms() -> None:
    """Block startup if any required permission is missing.

    Intended to be called once from ``Runtime.__init__`` (or ``execute_*``).
    On any missing perm: prints the banner and exits with status 1. Otherwise
    returns silently. Input Monitoring (undetectable) never blocks."""
    status = check_perms()
    missing_perms = missing(status)
    if not missing_perms:
        return
    print_onboarding_banner(missing_perms)
    raise SystemExit(1)
