"""case-sdk — dual-mode runtime for desktop GUI automation."""

from case_sdk.perms import check_perms, ensure_perms
from case_sdk.protocol import (
    Action,
    ClientError,
    Envelope,
    ProcedureError,
    Sensor,
)
from case_sdk.discovery import ProcedureNotFound, list_procedures, resolve_procedure
from case_sdk.inspect import InspectError, app_tree, find_elements
from case_sdk.runtime import Runtime
from case_sdk.steps import StepsFormatError, load_steps_file
from case_sdk.verify import VerifyContext, list_verifiers, run_verify

__version__ = "0.1.0"

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
