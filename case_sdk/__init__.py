"""case-sdk — dual-mode runtime for desktop GUI automation.

Imports are split into a linux-clean core (protocol, steps, discovery, verify)
and a macOS-only group (perms, inspect, runtime — they chain into pyautogui /
atomacos). The macOS-only group is wrapped in try/except so non-mac consumers
(e.g. a linux server importing case_sdk.protocol + case_sdk.steps) don't
ImportError at package load.

On macOS with the full deps installed, behaviour is unchanged.
"""

# Linux-clean core.
from case_sdk.protocol import (
    Action,
    ClientError,
    Envelope,
    ProcedureError,
    Sensor,
)
from case_sdk.discovery import ProcedureNotFound, list_procedures, resolve_procedure
from case_sdk.steps import StepsFormatError, load_steps_file
from case_sdk.verify import VerifyContext, list_verifiers, run_verify

__version__ = "0.1.1"

# macOS-only — depend on pyautogui / atomacos. Skipped on linux so consumers
# that only need protocol + steps can import case_sdk cleanly.
try:
    from case_sdk.perms import check_perms, ensure_perms
    from case_sdk.inspect import InspectError, app_tree, find_elements
    from case_sdk.runtime import Runtime
except ImportError:  # pragma: no cover — non-macOS path
    check_perms = ensure_perms = None  # type: ignore[assignment]
    InspectError = app_tree = find_elements = None  # type: ignore[assignment]
    Runtime = None  # type: ignore[assignment]

__all__ = [
    "Runtime",
    "Action",
    "Sensor",
    "Envelope",
    "ClientError",
    "ProcedureError",
    "ProcedureNotFound",
    "list_procedures",
    "resolve_procedure",
    "InspectError",
    "app_tree",
    "find_elements",
    "load_steps_file",
    "StepsFormatError",
    "VerifyContext",
    "run_verify",
    "list_verifiers",
    "check_perms",
    "ensure_perms",
    "__version__",
]
