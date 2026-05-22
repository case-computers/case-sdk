"""case-sdk — dual-mode runtime for desktop GUI automation.

Linux-clean core (protocol, steps, discovery, verify) imports unconditionally.
The macOS-only group (perms, inspect, runtime) is guarded so non-mac consumers
can still import the protocol + steps modules.
"""

import sys

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

__version__ = "0.1.3"


def _macos_only_stub(_name: str):
    def _raise(*_args, **_kwargs):
        raise RuntimeError(
            f"case_sdk.{_name} requires macOS; sys.platform={sys.platform!r}. "
            "Install case-sdk in a macOS environment to use this feature."
        )
    return _raise


# macOS-only — depend on pyautogui / atomacos. Skipped on linux so consumers
# that only need protocol + steps can import case_sdk cleanly.
try:
    from case_sdk.perms import check_perms, ensure_perms
    from case_sdk.inspect import InspectError, app_tree, find_elements
    from case_sdk.runtime import Runtime
except ImportError:  # pragma: no cover — non-macOS path
    Runtime = _macos_only_stub("Runtime")  # type: ignore[assignment]
    app_tree = _macos_only_stub("app_tree")
    find_elements = _macos_only_stub("find_elements")
    check_perms = _macos_only_stub("check_perms")
    ensure_perms = _macos_only_stub("ensure_perms")
    InspectError = _macos_only_stub("InspectError")  # type: ignore[assignment]

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
