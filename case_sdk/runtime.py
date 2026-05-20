"""Runtime — the public API.

Two modes:
  - ``rt.execute_file(path, **params)`` — runs a local procedure file.
  - ``rt.execute(name, **params)``      — streams a procedure from a
                                          remote WebSocket server
                                          (requires ``api_key``).

The constructor's ``api_key`` decides whether remote mode is available.
Both modes share the same ``Harness`` instance so the same set of action /
sensor primitives is dispatched regardless of where the procedure lives.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Literal

from case_sdk.drivers.local import LocalDriver
from case_sdk.drivers.remote import RemoteDriver
from case_sdk.harness.executor import Harness
from case_sdk.harness.gui import build_default_harness
from case_sdk.discovery import ProcedureNotFound, resolve_procedure
from case_sdk.steps import is_steps_file, load_steps_file, steps_to_procedure


def enrich_params(params: dict[str, Any]) -> dict[str, Any]:
    """Add derived procedure params (e.g. clip basename from filepath)."""
    out = dict(params)
    if "filepath" in out and "clip_basename" not in out:
        out["clip_basename"] = os.path.basename(str(out["filepath"]))
    if "page" in out and "page_title" not in out:
        # EH page tabs: match AXCheckBox title case-insensitively; common titles
        # are Title Case ("Edit", "Media Pool").
        page = str(out["page"]).strip()
        out["page_title"] = page[:1].upper() + page[1:] if page else page
    return out


class Runtime:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str = "ws://localhost:8000",
        harness: Harness | None = None,
        verify_mode: Literal["soft", "hard"] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        env_mode = os.environ.get("CASE_SDK_VERIFY_MODE", "soft")
        self.verify_mode: Literal["soft", "hard"] = (
            verify_mode if verify_mode is not None else env_mode  # type: ignore[assignment]
        )
        if self.verify_mode not in ("soft", "hard"):
            self.verify_mode = "soft"
        self.harness = harness or build_default_harness()
        self.local_driver = LocalDriver(self.harness)
        self.remote_driver: RemoteDriver | None = (
            RemoteDriver(self.harness, api_base=api_base, api_key=api_key)
            if api_key
            else None
        )

    def execute_file(self, path: str | Path, **params: Any) -> dict[str, Any]:
        """Run a local procedure file or discovered name. No API key required."""
        params = enrich_params(params)
        try:
            resolved = resolve_procedure(str(path))
        except ProcedureNotFound:
            resolved = Path(path).resolve()
            if not resolved.exists():
                raise

        if is_steps_file(resolved):
            doc = load_steps_file(resolved)
            return self.local_driver.execute(
                steps_to_procedure(doc, verify_mode=self.verify_mode),
                params,
            )

        spec = importlib.util.spec_from_file_location(resolved.stem, resolved)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load {resolved}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "procedure"):
            raise AttributeError(
                f"{resolved} must define a `procedure(params)` function "
                f"or be a .json step list"
            )
        return self.local_driver.execute(module.procedure, params)

    def execute(self, procedure_name: str, **params: Any) -> dict[str, Any]:
        """Run a named procedure on a remote WebSocket server. Requires api_key."""
        if self.remote_driver is None:
            raise RuntimeError(
                "execute() requires api_key; use execute_file() for local procedures"
            )
        return self.remote_driver.execute(procedure_name, params)
