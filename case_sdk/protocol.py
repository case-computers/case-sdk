"""Wire envelope shared by LocalDriver and RemoteDriver.

See ``Envelope.from_json`` for the schema; each message type's payload
shape is documented on its builder below. Procedures yield ``Action`` /
``Sensor`` / ``Verify`` instances which the driver turns into envelopes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PROTOCOL_VERSION = 1

MessageType = Literal[
    "hello",
    "action",
    "sensor",
    "verify",
    "result",
    "error",
    "done",
    "ping",
    "pong",
]


class Action:
    """A primitive the harness should execute (click, type, hotkey, ...).

    Procedures yield this; drivers translate it to an `action` envelope. The
    leading argument (the action name) is positional-only so that procedures
    can pass kwargs like `name=` without collision: e.g.
    ``Action("open_app", name="TextEdit")`` → ``self.args == {"name": "TextEdit"}``.
    """

    __slots__ = ("name", "args")

    def __init__(self, _action_name: str, /, **args: Any) -> None:
        self.name = _action_name
        self.args = args

    def __repr__(self) -> str:
        return f"Action({self.name!r}, args={self.args!r})"


class Sensor:
    """A read-only state query the harness should evaluate.

    Procedures yield this; the value returned to `gen.send(...)` is the
    `observation` field from the corresponding `result` envelope. The leading
    argument (the sensor name) is positional-only — see Action for rationale.
    """

    __slots__ = ("name", "args")

    def __init__(self, _sensor_name: str, /, **args: Any) -> None:
        self.name = _sensor_name
        self.args = args

    def __repr__(self) -> str:
        return f"Sensor({self.name!r}, args={self.args!r})"


class Verify:
    """A verify check the client should run locally (DaVinci sqlite, etc.).

    Like Sensor but routed through ``case_sdk.verify.run_verify`` on the
    client rather than the harness. Server-side procedures yield this when
    ``verify_dispatch="yield"`` so verify steps run against the user's local
    state (their DaVinci Project.db, etc.) instead of the server's filesystem.
    """

    __slots__ = ("name", "args")

    def __init__(self, _verify_name: str, /, **args: Any) -> None:
        self.name = _verify_name
        self.args = args

    def __repr__(self) -> str:
        return f"Verify({self.name!r}, args={self.args!r})"


@dataclass
class Envelope:
    type: MessageType
    payload: dict[str, Any] = field(default_factory=dict)
    v: int = PROTOCOL_VERSION
    session_id: str = ""
    step_id: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "Envelope":
        obj = json.loads(data)
        return cls(
            type=obj["type"],
            payload=obj.get("payload", {}),
            v=obj.get("v", PROTOCOL_VERSION),
            session_id=obj.get("session_id", ""),
            step_id=obj.get("step_id", 0),
        )


# ---- Builders ------------------------------------------------------------
# Keep payload shapes consistent across the codebase. If you need a field on
# the wire, add it here, never inline at the call site.


def new_session_id() -> str:
    return str(uuid.uuid4())


def hello(
    procedure: str,
    params: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
) -> Envelope:
    return Envelope(
        type="hello",
        session_id=session_id or new_session_id(),
        payload={"procedure": procedure, "params": params or {}},
    )


def action_msg(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    session_id: str = "",
    step_id: int = 0,
) -> Envelope:
    return Envelope(
        type="action",
        session_id=session_id,
        step_id=step_id,
        payload={"name": name, "args": args or {}},
    )


def sensor_msg(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    session_id: str = "",
    step_id: int = 0,
) -> Envelope:
    return Envelope(
        type="sensor",
        session_id=session_id,
        step_id=step_id,
        payload={"name": name, "args": args or {}},
    )


def verify_msg(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    session_id: str = "",
    step_id: int = 0,
) -> Envelope:
    """Server → client: run this verifier locally and reply with the result."""
    return Envelope(
        type="verify",
        session_id=session_id,
        step_id=step_id,
        payload={"name": name, "args": args or {}},
    )


def result_msg(
    ok: bool,
    observation: dict[str, Any] | None = None,
    error: str | None = None,
    *,
    session_id: str = "",
    step_id: int = 0,
) -> Envelope:
    payload: dict[str, Any] = {"ok": ok, "observation": observation or {}}
    if error is not None:
        payload["error"] = error
    return Envelope(
        type="result",
        session_id=session_id,
        step_id=step_id,
        payload=payload,
    )


def error_msg(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    *,
    session_id: str = "",
    step_id: int = 0,
) -> Envelope:
    return Envelope(
        type="error",
        session_id=session_id,
        step_id=step_id,
        payload={"code": code, "message": message, "details": details or {}},
    )


def done_msg(
    success: bool,
    reason: str = "",
    result: dict[str, Any] | None = None,
    *,
    session_id: str = "",
) -> Envelope:
    return Envelope(
        type="done",
        session_id=session_id,
        payload={"success": success, "reason": reason, "result": result or {}},
    )


def ping_msg(session_id: str = "") -> Envelope:
    return Envelope(type="ping", session_id=session_id)


def pong_msg(session_id: str = "") -> Envelope:
    return Envelope(type="pong", session_id=session_id)


# ---- Exception types ----------------------------------------------------


class ClientError(Exception):
    """Raised inside a procedure when a harness call failed.

    Carries an optional `code` string (e.g. "E_TIMEOUT", "E_UNKNOWN_ACTION")
    so procedures can branch on the failure reason.
    """

    def __init__(self, message: str, code: str = "E_CLIENT") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProcedureError(Exception):
    """Raised by a Driver.execute() when the procedure itself failed.

    Wraps either an uncaught exception thrown inside the procedure (Mode A)
    or an `error` envelope received from the server (Mode B).
    """

    def __init__(self, message: str, code: str = "E_PROCEDURE") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
