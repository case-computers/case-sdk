"""Declarative step-list procedures (JSON).

Lets users and agents author automation without Python. A step file is::

    {
      "steps": [
        {"action": "open_app", "name": "TextEdit"},
        {"action": "wait", "ms": 500},
        {"sensor": "window_title", "save_as": "title"}
      ],
      "return": {"success": true}
    }

Sensor steps may set ``save_as`` to store the observation in params for
later ``${name}`` substitution.

The optional top-level ``return`` value is the procedure result (after
``params`` substitution). Steps may use ``${param_name}`` placeholders in
string values, replaced from the ``params`` dict passed to ``execute_file``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from case_sdk.protocol import Action, ClientError, Sensor
from case_sdk.verify import VerifyContext, run_verify
from case_sdk.verify.runner import VerifyError

_PARAM_RE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class StepsFormatError(ValueError):
    pass


def load_steps_file(path: str | Path) -> dict[str, Any]:
    """Load and validate a ``.json`` step procedure from disk."""
    resolved = Path(path).resolve()
    text = resolved.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise StepsFormatError(f"invalid JSON in {resolved}: {e}") from e
    if not isinstance(doc, dict):
        raise StepsFormatError(f"{resolved}: root must be a JSON object")
    if "steps" not in doc:
        raise StepsFormatError(f"{resolved}: missing 'steps' array")
    if not isinstance(doc["steps"], list):
        raise StepsFormatError(f"{resolved}: 'steps' must be an array")
    return doc


def substitute_params(value: Any, params: dict[str, Any]) -> Any:
    """Replace ``${name}`` in strings recursively."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in params:
                raise StepsFormatError(f"undefined param ${{{key}}}")
            return str(params[key])

        return _PARAM_RE.sub(repl, value)
    if isinstance(value, list):
        return [substitute_params(v, params) for v in value]
    if isinstance(value, dict):
        return {k: substitute_params(v, params) for k, v in value.items()}
    return value


def steps_to_procedure(
    doc: dict[str, Any],
    *,
    verify_mode: str = "soft",
) -> Callable[[dict[str, Any]], Any]:
    """Build a ``procedure(params)`` callable from a step document."""

    mode = str(doc.get("verify_mode", verify_mode))
    if mode not in ("soft", "hard"):
        raise StepsFormatError(f"verify_mode must be 'soft' or 'hard', got {mode!r}")
    verify_ctx = VerifyContext(mode=mode)  # type: ignore[arg-type]

    def procedure(params: dict[str, Any]) -> Any:
        merged = dict(params)
        for raw_step in doc["steps"]:
            if not isinstance(raw_step, dict):
                raise StepsFormatError(f"each step must be an object, got {type(raw_step)}")
            step = substitute_params(raw_step, merged)
            if "verify" in step:
                vname = step.pop("verify")
                if not isinstance(vname, str):
                    raise StepsFormatError("verify name must be a string")
                try:
                    run_verify(vname, step, verify_ctx)
                except ClientError:
                    raise
                except VerifyError as e:
                    if verify_ctx.mode == "hard":
                        raise ClientError(str(e), code="E_VERIFY") from e
                continue
            if "action" in step:
                name = step.pop("action")
                if not isinstance(name, str):
                    raise StepsFormatError("action name must be a string")
                yield Action(name, **step)
            elif "sensor" in step:
                name = step.pop("sensor")
                save_as = step.pop("save_as", None)
                if not isinstance(name, str):
                    raise StepsFormatError("sensor name must be a string")
                observation = yield Sensor(name, **step)
                if save_as:
                    if not isinstance(save_as, str):
                        raise StepsFormatError("save_as must be a string")
                    if isinstance(observation, dict):
                        merged[save_as] = observation.get("value", observation)
                    else:
                        merged[save_as] = observation
            else:
                raise StepsFormatError(
                    "step must contain 'action', 'sensor', or 'verify' key"
                )
        ret = doc.get("return", {"success": True})
        out = substitute_params(ret, merged)
        if isinstance(out, dict):
            out = dict(out)
            out["verify_results"] = list(verify_ctx.results)
        return out

    return procedure


def is_steps_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".json"
