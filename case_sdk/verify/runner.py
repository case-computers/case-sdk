"""Verify step dispatcher."""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable

from case_sdk.protocol import ClientError
from case_sdk.verify.context import VerifyContext

log = logging.getLogger("case_sdk.verify")

Handler = Callable[[dict[str, Any], VerifyContext], dict[str, Any]]

_REGISTRY: dict[str, Handler] = {}


class VerifyError(Exception):
    """Internal verify failure before soft/hard handling."""


def register(name: str):
    def deco(fn: Handler) -> Handler:
        _REGISTRY[name] = fn
        return fn

    return deco


def list_verifiers() -> list[str]:
    return sorted(_REGISTRY.keys())


def run_verify(name: str, args: dict[str, Any], ctx: VerifyContext) -> dict[str, Any]:
    """Run one verify step. Returns a result dict (always, even on soft fail)."""
    handler = _REGISTRY.get(name)
    if handler is None:
        raise VerifyError(f"unknown verifier {name!r}; known: {list_verifiers()}")

    try:
        result = handler(args, ctx)
        ok = bool(result.get("ok", True))
        ctx.record_result(name, ok, result)
        if not ok:
            _handle_failure(name, result, ctx)
        return result
    except VerifyError as e:
        result = {"ok": False, "reason": str(e)}
        ctx.record_result(name, False, result)
        _handle_failure(name, result, ctx)
        return result


def _handle_failure(name: str, result: dict[str, Any], ctx: VerifyContext) -> None:
    msg = result.get("reason") or result.get("message") or f"verify {name} failed"
    if ctx.mode == "hard":
        raise ClientError(msg, code="E_VERIFY")
    log.warning("[verify] %s: %s", name, msg)
    print(f"[verify] {name}: {msg}", file=sys.stderr)
