"""DaVinci Resolve adapters."""

from case_sdk.apps.davinci import db as db

# Lazy on the actions module — it transitively pulls in PyObjC
# (``ApplicationServices`` + ``Cocoa``) which only exist on macOS. Linux
# consumers (case-api running on Cloud Run) need `db` for verify dispatch
# but never execute the harness, so a missing PyObjC must not break import.
try:
    from case_sdk.apps.davinci.actions import register
except ImportError:  # pragma: no cover — non-macOS path
    register = None  # type: ignore[assignment]

__all__ = ["db", "register"]
