"""case-sdk — dual-mode runtime for desktop GUI automation."""

from case_sdk.perms import check_perms, ensure_perms
from case_sdk.protocol import (
    Action,
    ClientError,
    Envelope,
    ProcedureError,
    Sensor,
)
from case_sdk.runtime import Runtime

__version__ = "0.1.0"

__all__ = [
    "Runtime",
    "Action",
    "Sensor",
    "Envelope",
    "ClientError",
    "ProcedureError",
    "check_perms",
    "ensure_perms",
    "__version__",
]
