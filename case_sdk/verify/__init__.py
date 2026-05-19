"""Post-action verification — DB, AX, and app-specific checks.

Use from JSON procedures via ``verify`` steps, or from Python::

    from case_sdk.verify import VerifyContext, run_verify

    ctx = VerifyContext(mode="hard")
    run_verify("davinci.db_snapshot", {"as": "t0"}, ctx)
    # ... run actions ...
    run_verify("davinci.db_delta", {"field": "timeline_video", "baseline": "t0"}, ctx)

``mode`` is ``"soft"`` (log warning, continue) or ``"hard"`` (raise
:class:`case_sdk.protocol.ClientError` with code ``E_VERIFY``).
"""

from __future__ import annotations

from case_sdk.verify import davinci as _davinci  # noqa: F401 — register handlers
from case_sdk.verify.context import VerifyContext, VerifyMode
from case_sdk.verify.runner import VerifyError, list_verifiers, run_verify

__all__ = [
    "VerifyContext",
    "VerifyMode",
    "VerifyError",
    "run_verify",
    "list_verifiers",
]
