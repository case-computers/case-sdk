"""Runtime — the public API.

Two modes:
  - ``rt.execute_file(path, **params)`` — Mode A, runs a local procedure file.
  - ``rt.execute(name, **params)``      — Mode B, streams from the case-api
                                          server (requires ``api_key``).

The constructor's ``api_key`` decides whether Mode B is available. Both
modes share the same ``Harness`` instance so the same set of action /
sensor primitives is dispatched regardless of where the procedure lives.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from case_sdk.drivers.local import LocalDriver
from case_sdk.drivers.remote import RemoteDriver
from case_sdk.harness.executor import Harness
from case_sdk.harness.gui import build_default_harness


class Runtime:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str = "ws://localhost:8000",
        harness: Harness | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.harness = harness or build_default_harness()
        self.local_driver = LocalDriver(self.harness)
        self.remote_driver: RemoteDriver | None = (
            RemoteDriver(self.harness, api_base=api_base, api_key=api_key)
            if api_key
            else None
        )

    def execute_file(self, path: str | Path, **params: Any) -> dict[str, Any]:
        """Run a local procedure file. No server, no API key required."""
        resolved = Path(path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(resolved)
        spec = importlib.util.spec_from_file_location(resolved.stem, resolved)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load {resolved}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "procedure"):
            raise AttributeError(
                f"{resolved} must define a `procedure(params)` function"
            )
        return self.local_driver.execute(module.procedure, params)

    def execute(self, procedure_name: str, **params: Any) -> dict[str, Any]:
        """Run a named procedure on the case-api server. Requires api_key."""
        if self.remote_driver is None:
            raise RuntimeError(
                "execute() requires api_key; use execute_file() for local procedures"
            )
        return self.remote_driver.execute(procedure_name, params)
