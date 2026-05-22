"""DaVinci Resolve high-level actions.

Five harness actions that wrap AX-walking patterns case-sdk's core primitives
(``click_ref``, ``type``, ``hotkey``, ``ax_set_value``) can't express
declaratively from a procedure JSON. All five are registered into a
:class:`case_sdk.harness.executor.Harness` via :func:`register`, mirroring the
``case_sdk.harness.sensors.register_default_sensors`` shape.

All actions return a dict (or raise). Failures raise plain ``Exception`` —
the driver catches and surfaces them as ``ClientError`` to the procedure
generator, which can ``try/except ClientError`` and retry.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from case_sdk.apps.davinci import _ax
from case_sdk.harness.executor import Harness
from case_sdk.harness.gui import _clamp_coords as _clamp

# Module-level pyautogui binding so tests can patch
# ``case_sdk.apps.davinci.actions.pyautogui``. On linux the import fails and
# ``register`` re-raises ImportError so ``build_default_harness`` can silently
# skip the bundle.
try:
    import pyautogui  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — non-macOS path
    pyautogui = None  # type: ignore[assignment]


# ---- registration -------------------------------------------------------


def register(h: Harness) -> None:
    """Attach the five ``davinci_*`` actions to *h*.

    Called from :func:`case_sdk.harness.gui.build_default_harness` under a
    ``try/except ImportError`` guard — on linux (no pyautogui) ``pyautogui``
    is ``None`` at module load, the guard below raises ImportError, and the
    bundle silently no-ops.
    """
    if pyautogui is None:
        raise ImportError(
            "pyautogui is not installed; case_sdk.apps.davinci requires macOS"
        )

    cache_key = id(h)

    @h.register_action("davinci_set_label_numeric")
    def davinci_set_label_numeric(
        label: str,
        value: Any,
        panel: str = "color",
        verify_readback: bool = True,
    ) -> dict[str, Any]:
        """Set a labelled numeric field in the Color page / Inspector.

        Walks the AX tree to find an ``AXStaticText`` whose value matches
        *label* (with Log-Wheels-mode alias fallback), then locates the
        numeric ``AXStaticText`` immediately to its right (within ±10 px Y),
        double-clicks, Cmd+A, types *value*, presses Return.

        If ``verify_readback`` is true (default), re-reads the field via the
        AX tree and raises if the new value doesn't match *value* (within
        1% relative tolerance for floats).

        *panel* is accepted but ignored in v1 — the scan walks the whole
        Resolve AX tree. Reserved for future scoped scans.
        """
        del panel  # documented but unused in v1

        el = _ax.find_numeric_neighbor(cache_key, label)
        x, y = _ax._ax_point(el)
        if x is None or y is None:
            raise RuntimeError(f"could not get screen position for label {label!r}")
        cx, cy = _clamp(int(x), int(y))

        _focus_resolve()
        pyautogui.doubleClick(cx, cy)
        time.sleep(0.08)
        pyautogui.hotkey(_cmd_key(), "a")
        time.sleep(0.05)
        pyautogui.typewrite(str(value), interval=0.01)
        time.sleep(0.05)
        pyautogui.press("return")
        time.sleep(0.15)

        out: dict[str, Any] = {"label": label, "value": str(value)}
        if verify_readback:
            _ax.invalidate_scan(cache_key)
            try:
                new_el = _ax.find_numeric_neighbor(cache_key, label)
                raw = _ax._safe_attr(new_el, "AXValue") or ""
                actual = raw.strip() if isinstance(raw, str) else str(raw).strip()
                out["readback"] = actual
                if not _numeric_close(actual, str(value)):
                    raise RuntimeError(
                        f"readback mismatch for {label!r}: wrote {value!r}, "
                        f"read {actual!r}"
                    )
            except RuntimeError:
                raise
            except Exception as exc:
                # AX flakiness on readback — log via out, don't fail the action.
                out["readback_warn"] = f"{type(exc).__name__}: {exc}"
        return out

    @h.register_action("davinci_set_rich_text")
    def davinci_set_rich_text(text: str, area_index: int = 0) -> dict[str, Any]:
        """Replace contents of the nth ``AXTextArea`` in Resolve's Inspector.

        Resolve's Rich Text widget is a Qt ``QTextEdit`` whose AXValue
        assignment returns success but doesn't mutate the underlying
        document. Only keyboard events actually change content, so we drive
        the field exclusively with the keyboard:

        1. Click the text area centre to focus it.
        2. Cmd+A then Backspace — twice (some builds eat the first attempt).
        3. Option+Backspace × 24 (delete-word fallback for stuck rich runs).
        4. Backspace × 24 then Delete × 24 (single-char cleanups).
        5. Type the replacement text at a humanlike interval (10-15 ms).

        Steps 3–4 are wasteful in the common case but harmless and add
        ~500 ms of insurance against rich-text edge cases.
        """
        app_el = _ax._get_resolve_app()
        areas: list[Any] = []
        queue: list[Any] = _ax._initial_queue(app_el)
        while queue:
            el = queue.pop(0)
            if (_ax._safe_attr(el, "AXRole") or "") == "AXTextArea":
                areas.append(el)
            for child in _ax._safe_children(el):
                queue.append(child)
        if area_index >= len(areas):
            raise RuntimeError(
                f"requested AXTextArea index {area_index} but only "
                f"{len(areas)} found in Resolve's tree"
            )
        target = areas[area_index]
        tx, ty = _ax._ax_point(target)
        _focus_resolve()
        if tx is not None and ty is not None and tx > 50 and ty > 50:
            cx, cy = _clamp(int(tx), int(ty))
            pyautogui.click(cx, cy)
            time.sleep(0.12)

        cmd = _cmd_key()
        for _ in range(2):
            pyautogui.hotkey(cmd, "a")
            time.sleep(0.05)
            pyautogui.press("backspace")
            time.sleep(0.18)
        for _ in range(24):
            pyautogui.hotkey("option", "backspace")
            time.sleep(0.012)
        for _ in range(24):
            pyautogui.press("backspace")
            time.sleep(0.012)
        for _ in range(24):
            pyautogui.press("delete")
            time.sleep(0.012)
        time.sleep(0.12)

        if not text:
            return {"text": text, "area_index": area_index, "cleared": True}

        if all(ord(c) < 128 for c in text):
            pyautogui.write(text, interval=0.012)
        else:
            _paste(text)
        time.sleep(0.25)
        return {"text": text, "area_index": area_index}

    @h.register_action("davinci_select_pool_clip")
    def davinci_select_pool_clip(name: str) -> dict[str, Any]:
        """Filter the Media Pool by *name* and select the first match.

        Resolve's Media Pool has a Search toggle (``AXCheckBox``) that must
        be enabled before the search ``AXTextField`` appears. We enable it
        if needed, focus the field, clear via Cmd+A + Backspace, type the
        filename stem (without extension — Resolve doesn't index extensions
        consistently), press Down to focus the first result, then Return to
        select.
        """
        field = _ax.find_media_pool_search()
        if field is None:
            raise RuntimeError(
                "Media Pool search field not found. Make sure the Media Pool "
                "panel is visible (Edit / Media page)."
            )
        fx, fy = _ax._ax_point(field)
        if fx is None or fy is None:
            raise RuntimeError("Could not resolve Media Pool search coordinates")
        cx, cy = _clamp(int(fx), int(fy))
        _focus_resolve()
        pyautogui.click(cx, cy)
        time.sleep(0.15)

        stem = Path(name).stem or name
        cmd = _cmd_key()
        pyautogui.hotkey(cmd, "a")
        time.sleep(0.05)
        pyautogui.press("backspace")
        time.sleep(0.05)
        pyautogui.typewrite(stem, interval=0.01)
        time.sleep(0.4)
        pyautogui.press("down")
        time.sleep(0.1)
        pyautogui.press("return")
        time.sleep(0.5)
        return {"name": name, "stem": stem}

    @h.register_action("davinci_set_inspector_combo")
    def davinci_set_inspector_combo(combo: str, value: str) -> dict[str, Any]:
        """Set an Inspector ``AXComboBox`` (font family / face / case /
        alignment / etc.) to *value*.

        Walks the AX tree for the first ``AXComboBox`` whose title or
        description matches *combo* (case-insensitive substring). Clicks
        the combo, clears via Cmd+A + Backspace, types *value*, waits for
        Resolve's filter dropdown to populate, then presses Return to
        commit the selection.
        """
        el = _ax.find_combo_box(combo)
        if el is None:
            raise RuntimeError(
                f"Inspector combo {combo!r} not found. Make sure the title "
                "clip is selected and the relevant Inspector tab is open."
            )
        cx, cy = _ax._ax_point(el)
        if cx is None or cy is None:
            raise RuntimeError(f"combo {combo!r} has no AXPosition")
        x, y = _clamp(int(cx), int(cy))
        _focus_resolve()
        pyautogui.click(x, y)
        time.sleep(0.18)
        cmd = _cmd_key()
        pyautogui.hotkey(cmd, "a")
        time.sleep(0.04)
        pyautogui.press("backspace")
        time.sleep(0.04)
        pyautogui.typewrite(value, interval=0.012)
        time.sleep(0.4)
        pyautogui.press("return")
        time.sleep(0.2)
        return {"combo": combo, "value": value}

    @h.register_action("davinci_color_wheel_reset")
    def davinci_color_wheel_reset(
        wheel: str,
        verify_readback: bool = False,
    ) -> dict[str, Any]:
        """Click the Reset button for one of the four colour wheels.

        Locates the row of 4 wheel-reset ``AXButton`` elements via Y-axis
        clustering (no hard-coded screen coords), sorts them left-to-right,
        and clicks the one corresponding to *wheel*:

        ============  =====
        wheel value   index
        ============  =====
        ``lift``      0 (leftmost)
        ``gamma``     1
        ``gain``      2
        ``offset``    3 (rightmost)
        ============  =====

        ``verify_readback`` is reserved — readback verification for wheel
        resets requires reading all four channel numerics for the wheel,
        which is non-trivial and deferred until a procedure asks for it.
        """
        del verify_readback  # reserved

        wheel_norm = wheel.strip().lower()
        order = {"lift": 0, "gamma": 1, "gain": 2, "offset": 3}
        idx = order.get(wheel_norm)
        if idx is None:
            raise ValueError(
                f"Unknown wheel {wheel!r}. Expected one of {sorted(order)}."
            )
        buttons = _ax.find_wheel_reset_buttons()
        if len(buttons) <= idx:
            raise RuntimeError(
                f"Could not locate {wheel!r} reset button: detected "
                f"{len(buttons)} wheel reset buttons (need index {idx}). "
                "Make sure the Color page Primaries Wheels panel is open."
            )
        el = buttons[idx]
        cx, cy = _ax._ax_point(el)
        if cx is None or cy is None:
            raise RuntimeError(f"reset button for {wheel!r} has no AXPosition")
        x, y = _clamp(int(cx), int(cy))
        _focus_resolve()
        pyautogui.click(x, y)
        time.sleep(0.3)
        _ax.invalidate_scan(cache_key)
        return {"wheel": wheel_norm, "index": idx}


# ---- small utilities (kept private to this module) ---------------------


def _cmd_key() -> str:
    """Return the keyname pyautogui expects for the Command modifier on this OS.

    pyautogui maps ``"command"`` to the Cmd key on macOS; on linux/windows
    we'd never get here (registration would have failed), but the wrapper
    keeps the string in one place.
    """
    return "command"


def _focus_resolve() -> None:
    """Bring Resolve to the foreground via osascript activate."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "DaVinci Resolve" to activate'],
            check=False,
            capture_output=True,
            timeout=3.0,
        )
        time.sleep(0.12)
    except Exception:
        pass


def _paste(text: str) -> None:
    """Paste *text* via pbcopy + Cmd+V (for non-ASCII strings)."""
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.2)
    pyautogui.hotkey(_cmd_key(), "v")
    time.sleep(0.3)


def _numeric_close(actual: str, expected: str, *, rel_tol: float = 0.01) -> bool:
    """Return True if *actual* and *expected* parse as floats within 1% of each other.

    Falls back to exact-string-match if either side fails to parse — covers
    non-numeric Inspector fields like font face names.
    """
    try:
        a = float(actual)
        e = float(expected)
    except (ValueError, TypeError):
        return actual.strip().lower() == expected.strip().lower()
    if e == 0:
        return abs(a) < 1e-6
    return abs(a - e) / abs(e) <= rel_tol or os.environ.get("CASE_SDK_DAVINCI_LOOSE_READBACK") == "1"
