"""LocalDriver — runs a procedure generator in-process against a local harness.

Mode A path: no server, no network, no API key. The procedure file lives on the
user's disk; the driver imports it and walks it yield-by-yield.

Error boundary (P9):
    If a harness call fails, we surface the failure *into* the generator
    via ``gen.throw(ClientError(...))`` so the procedure can
    ``try/except ClientError`` and retry. Anything the procedure does not
    catch (or any other exception class it raises) is re-thrown to the
    caller as ``ProcedureError`` — the procedure is dead and the session
    is over.

    Per-step timeout: each harness call runs on a worker thread bounded
    by ``step_timeout`` seconds. Exceeding it → ``ClientError("E_TIMEOUT")``.

The same shape applies to RemoteDriver — see drivers/remote.py.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable

from case_sdk.drivers.base import Driver
from case_sdk.harness.executor import Harness
from case_sdk.protocol import Action, ClientError, ProcedureError, Sensor

DEFAULT_STEP_TIMEOUT = float(os.environ.get("CASE_SDK_STEP_TIMEOUT", "30"))


class LocalDriver(Driver):
    def __init__(
        self,
        harness: Harness,
        *,
        step_timeout: float = DEFAULT_STEP_TIMEOUT,
    ) -> None:
        self.harness = harness
        self.step_timeout = step_timeout

    def execute(
        self,
        generator_fn: Callable[[dict[str, Any]], Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        gen = generator_fn(params)

        try:
            yielded = next(gen)
        except StopIteration as stop:
            return _coerce_return(stop.value)
        except Exception as e:
            # Procedure raised before yielding once. Wrap and surface.
            raise ProcedureError(f"{type(e).__name__}: {e}") from e

        # One executor for the lifetime of this run keeps timing
        # measurements deterministic — no per-step thread spin-up cost
        # bleed into the timeout budget.
        with ThreadPoolExecutor(max_workers=1) as pool:
            while True:
                client_err: ClientError | None = None
                result: dict[str, Any] = {}

                # Yield-type check happens *before* the try/except so a
                # `yield "garbage"` from a buggy procedure surfaces as a
                # raw TypeError (matches legacy behaviour). Only actual
                # harness failures flow through gen.throw(ClientError).
                if not isinstance(yielded, (Action, Sensor)):
                    raise TypeError(
                        f"procedure must yield Action or Sensor, got "
                        f"{type(yielded).__name__}: {yielded!r}"
                    )

                try:
                    result = self._invoke_harness(yielded, pool)
                except ClientError as e:
                    client_err = e
                except ProcedureError:
                    raise
                except Exception as e:
                    # Any other exception out of the harness is treated as a
                    # client-side failure — wrap and surface to the
                    # procedure via gen.throw so it can choose to retry.
                    client_err = ClientError(f"{type(e).__name__}: {e}")

                try:
                    if client_err is not None:
                        yielded = gen.throw(client_err)
                    else:
                        yielded = gen.send(result)
                except StopIteration as stop:
                    return _coerce_return(stop.value)
                except ClientError as e:
                    # Procedure re-raised the ClientError (chose not to
                    # retry). Surface as procedure failure.
                    raise ProcedureError(
                        f"procedure aborted on client error: {e}",
                        code=e.code,
                    ) from e
                except ProcedureError:
                    raise
                except Exception as e:
                    raise ProcedureError(f"{type(e).__name__}: {e}") from e

    def _invoke_harness(self, yielded: Any, pool: ThreadPoolExecutor) -> dict[str, Any]:
        if isinstance(yielded, Action):
            target = self.harness.execute_action
            args = (yielded.name, yielded.args)
        else:
            # Caller already verified isinstance(yielded, (Action, Sensor)),
            # so the only other case is Sensor.
            target = self.harness.execute_sensor
            args = (yielded.name, yielded.args)

        fut = pool.submit(target, *args)
        try:
            return fut.result(timeout=self.step_timeout) or {}
        except FutureTimeoutError as e:
            # The worker thread is still running; we leak it because the
            # harness might be doing real GUI work that we can't cancel
            # mid-flight. The pool dies with the run so the leak is
            # bounded.
            raise ClientError(
                f"step timed out after {self.step_timeout}s",
                code="E_TIMEOUT",
            ) from e


def _coerce_return(value: Any) -> dict[str, Any]:
    if value is None:
        return {"success": True}
    if isinstance(value, dict):
        return value
    return {"success": True, "value": value}
