"""Driver ABC.

A Driver advances a procedure (generator) one yield at a time. LocalDriver
runs it in-process; RemoteDriver pipes yields over a WebSocket to a server
that holds the procedure. From the procedure's perspective the two drivers
are interchangeable — same Action / Sensor yields, same result delivered to
`gen.send(...)`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class Driver(ABC):
    @abstractmethod
    def execute(
        self,
        generator_fn: Callable[[dict[str, Any]], Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the generator to completion. Return the procedure's return value."""
