"""Per-run verification state (named baselines, last snapshot)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

VerifyMode = Literal["soft", "hard"]


@dataclass
class VerifyContext:
    """Holds snapshots and options for one procedure run."""

    mode: VerifyMode = "soft"
    baselines: dict[str, Any] = field(default_factory=dict)
    last_snapshot: Any | None = None
    last_db_path: Any | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

    def record_result(self, name: str, ok: bool, detail: dict[str, Any]) -> None:
        self.results.append({"verify": name, "ok": ok, **detail})
