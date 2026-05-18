# case-sdk

Dual-mode runtime for desktop GUI automation on macOS.

- **Local mode** — write your own procedures in Python and run them on your machine.
- **Remote mode** — with an API key, stream verified procedures from the case-api server. The procedure source never lands on the client's disk.

Both modes share the same `Runtime` API.

## Install

From source (until the package is on PyPI):

```bash
git clone https://github.com/EmulatedHumanoid/case-sdk
cd case-sdk
pip install -e .
```

Grant Accessibility in *System Settings → Privacy & Security → Accessibility*. Then run `case-sdk doctor` to verify.

Screen Recording is only required if a procedure uses the `screenshot` sensor.

## Write a procedure

A procedure is a Python generator. It yields `Action` to do things and `Sensor` to read state. The driver runs each yield against the local harness and feeds the result back into the generator via `gen.send(...)`.

```python
# my_proc.py
from case_sdk import Action, Sensor

def procedure(params):
    yield Action("open_app", name="TextEdit")
    yield Action("wait", ms=500)
    yield Action("type", text="hello")
    yield Action("hotkey", combo=["cmd", "s"])
    title = yield Sensor("window_title")
    return {"success": True, "title": title["value"]}
```

## Run a local procedure

```python
from case_sdk import Runtime
Runtime().execute_file("my_proc.py")
```

Or from the shell:

```bash
case-sdk run my_proc.py
```

## Run a remote procedure

```python
from case_sdk import Runtime
rt = Runtime(api_key="sk_...")
rt.execute("add_clip_to_timeline", filepath="/path/to/clip.mov")
```

Or:

```bash
case-sdk init                                                  # one-time, stores the key
case-sdk run add_clip_to_timeline --param filepath=/x.mov
```

In remote mode the server holds the procedure. The runtime receives one action at a time over WebSocket, runs it locally, returns the observation, and waits for the next instruction.

## Built-in primitives

Actions: `open_app`, `click`, `type`, `hotkey`, `scroll`, `mouse_down`, `mouse_up`, `wait`, `ax_press`, `ax_set_value`.

Sensors: `window_title`, `ax_value`, `screenshot`.

## CLI

```
case-sdk run <path-or-name> [--key sk_xxx] [--api wss://...] [--param k=v]...
case-sdk init [--key sk_xxx] [--api wss://...] [--force]
case-sdk doctor
case-sdk --version
```

Configuration resolves in order: CLI flag → environment (`CASE_SDK_API_KEY`, `CASE_SDK_API_BASE`) → `~/.case-sdk/config.toml`.

## Error handling

Inside a procedure you can catch harness failures and decide what to do:

```python
from case_sdk import Action, ClientError

def procedure(params):
    try:
        yield Action("hotkey", combo=["cmd", "s"])
    except ClientError as e:
        if e.code == "E_TIMEOUT":
            return {"success": False, "reason": "save timed out"}
        raise
```

`ClientError` represents a recoverable harness failure (timeout, primitive raised). Uncaught exceptions and re-raised `ClientError`s surface to the caller of `Runtime.execute*` as `ProcedureError`.

## License

FSL-1.1-Apache-2.0. Source-available; converts to Apache 2.0 two years after each release. Using case-sdk to operate a competing hosted procedure-library service is not permitted; all other use is fine. See `LICENSE` for the full text.
