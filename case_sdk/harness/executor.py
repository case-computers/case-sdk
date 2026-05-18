"""Harness — registry of action + sensor implementations.

Drivers don't know how to click a mouse. They ask the harness, which looks up
the registered callable by name and invokes it. This keeps the dispatch layer
(Driver) decoupled from the OS-specific implementation (pyautogui, AX, etc.).
"""

from __future__ import annotations

from typing import Any, Callable


class UnknownActionError(KeyError):
    pass


class UnknownSensorError(KeyError):
    pass


class Harness:
    def __init__(self) -> None:
        self._actions: dict[str, Callable[..., dict[str, Any] | None]] = {}
        self._sensors: dict[str, Callable[..., dict[str, Any] | None]] = {}

    def register_action(self, name: str):
        def deco(fn: Callable[..., dict[str, Any] | None]):
            self._actions[name] = fn
            return fn

        return deco

    def register_sensor(self, name: str):
        def deco(fn: Callable[..., dict[str, Any] | None]):
            self._sensors[name] = fn
            return fn

        return deco

    def execute_action(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = self._actions.get(name)
        if fn is None:
            raise UnknownActionError(name)
        return fn(**args) or {}

    def execute_sensor(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = self._sensors.get(name)
        if fn is None:
            raise UnknownSensorError(name)
        return fn(**args) or {}

    def known_actions(self) -> list[str]:
        return sorted(self._actions.keys())

    def known_sensors(self) -> list[str]:
        return sorted(self._sensors.keys())
