#!/usr/bin/env python3
"""End-to-end case-sdk demo (Mode A, no server, no API key).

Shows the canonical SDK use:
  1. ``from case_sdk import Runtime, Action, Sensor``
  2. define a ``procedure(params)`` generator yielding Action / Sensor
  3. construct a Runtime
  4. run the generator and read the result

The procedure here opens TextEdit, types a message, reads the active
window title via a Sensor to verify focus, then saves with cmd+s. The
runner prints each step (yield → harness call → result) plus timings so
you can see exactly what the SDK is doing.

Run::

    .venv/bin/python scripts/test_procedure.py
    .venv/bin/python scripts/test_procedure.py --message "hello world"
    .venv/bin/python scripts/test_procedure.py --appname Safari --skip-perms
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from case_sdk import Action, ClientError, Runtime, Sensor, check_perms
from case_sdk.perms import missing, print_onboarding_banner


# ----- The procedure ------------------------------------------------------
# This is what an SDK user writes. Generator function, takes a `params`
# dict, yields Action or Sensor objects, returns a result dict. Each
# `yield` hands the action/sensor to the driver; the driver runs it via
# the local harness and the value returned to the `yield` expression is
# the observation dict the harness produced.


def demo_procedure(params: dict[str, Any]):
    """Open an app, type into it, verify focus via Sensor, save with cmd+s."""
    appname = params.get("appname", "TextEdit")
    message = params.get("message", "hi from case-sdk")

    # 1. Launch the app
    yield Action("open_app", name=appname)

    # 2. Give the system a moment to bring it forward
    yield Action("wait", ms=600)

    # 3. Mid-flight state check: read the frontmost-window title.
    title = yield Sensor("window_title")
    if appname.lower() not in title.get("value", "").lower():
        return {
            "success": False,
            "reason": f"focus check failed: title={title.get('value')!r}",
        }

    # 4. Type the message
    yield Action("type", text=message)

    # 5. Save. If the harness primitive raises, catch via ClientError so
    #    we can return a graceful failure rather than crashing the SDK.
    try:
        yield Action("hotkey", combo=["cmd", "s"])
    except ClientError as err:
        return {"success": False, "reason": str(err), "code": err.code}

    # 6. Let the save dialog appear
    yield Action("wait", ms=300)

    return {
        "success": True,
        "appname": appname,
        "title_seen": title.get("value", ""),
        "chars_typed": len(message),
    }


# ----- Runner with per-step trace -----------------------------------------
# Same loop LocalDriver runs internally, but with print statements so you
# can watch each yield + result. Use this when you're authoring a new
# procedure and want visibility. For production, just call
# ``rt.local_driver.execute(demo_procedure, params)`` directly.


def run_with_trace(rt: Runtime, proc, params: dict[str, Any]) -> dict[str, Any]:
    gen = proc(params)
    overall_start = time.monotonic()

    try:
        yielded = next(gen)
    except StopIteration as stop:
        return stop.value if stop.value is not None else {"success": True}

    step = 0
    while True:
        step += 1
        kind = "action" if isinstance(yielded, Action) else "sensor"
        args = ", ".join(f"{k}={v!r}" for k, v in yielded.args.items())
        print(f"  step {step:>2}  {kind} {yielded.name}({args})")
        t0 = time.monotonic()

        err: ClientError | None = None
        result: dict[str, Any] = {}
        try:
            if isinstance(yielded, Action):
                result = rt.harness.execute_action(yielded.name, yielded.args) or {}
            elif isinstance(yielded, Sensor):
                result = rt.harness.execute_sensor(yielded.name, yielded.args) or {}
            else:
                print(f"          ✗ procedure yielded a non-Action/Sensor: {type(yielded).__name__}")
                return {"success": False, "reason": "bad yield"}
        except Exception as e:
            err = ClientError(f"{type(e).__name__}: {e}")

        elapsed_ms = (time.monotonic() - t0) * 1000
        if err is not None:
            print(f"          ✗ {elapsed_ms:6.1f}ms  {err}")
        else:
            print(f"          ✓ {elapsed_ms:6.1f}ms  {result}")

        try:
            yielded = gen.throw(err) if err is not None else gen.send(result)
        except StopIteration as stop:
            ret = stop.value if stop.value is not None else {"success": True}
            if not isinstance(ret, dict):
                ret = {"success": True, "value": ret}
            total = (time.monotonic() - overall_start) * 1000
            print(f"\ntotal: {total:.0f}ms across {step} step(s)")
            return ret


# ----- Main ---------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--appname", default="TextEdit", help="app to open (default: TextEdit)")
    ap.add_argument("--message", default="hi from case-sdk", help="text to type")
    ap.add_argument(
        "--skip-perms",
        action="store_true",
        help="skip macOS perm check (actions may silently no-op without perms)",
    )
    args = ap.parse_args()

    print("case-sdk demo — Mode A (local, no server)\n")

    # 1. Construct a Runtime. No api_key = local procedures only.
    rt = Runtime()
    print(f"actions: {', '.join(rt.harness.known_actions())}")
    print(f"sensors: {', '.join(rt.harness.known_sensors())}")
    print()

    # 2. Perms gate. Runtime doesn't enforce this — call check_perms yourself.
    if not args.skip_perms:
        status = check_perms()
        m = missing(status)
        if m:
            print_onboarding_banner(m)
            print("\n(re-run with --skip-perms to bypass this gate)")
            return 1

    # 3. Run the demo procedure with a per-step trace.
    params = {"appname": args.appname, "message": args.message}
    print(f"running demo_procedure(params={params})\n")
    result = run_with_trace(rt, demo_procedure, params)

    # 4. Print final result.
    print("\nresult:")
    for k, v in result.items():
        print(f"  {k}: {v!r}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
