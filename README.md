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
pip install -e ".[mcp]"    # optional: MCP server for Claude Code / agents
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

Actions: `open_app`, `click`, `type`, `hotkey`, `scroll`, `mouse_down`, `mouse_up`, `wait`, `ax_press`, `ax_set_value`, `fn_key`, `drag`.

Sensors: `window_title`, `ax_value`, `screenshot`.

## Inspect the AX tree (authoring)

Discover UI elements before writing `ax_press` / `ax_set_value` steps:

```bash
case-sdk ax-tree --frontmost
case-sdk ax-tree "DaVinci Resolve" --depth 10
case-sdk ax-tree "DaVinci Resolve" --find --role AXButton --title-contains Append
```

Python API:

```python
from case_sdk import app_tree, find_elements

tree = app_tree("TextEdit", max_depth=8)
refs = find_elements("DaVinci Resolve", role="AXButton", title_contains="Save")
```

Each node includes `role`, `title`, and optional `path` (child indices). Use those fields in `element_ref` when yielding AX actions.

## Verification (Project.db and more)

After GUI actions, confirm state with **verify** steps in JSON procedures (or call from Python):

```json
{
  "verify_mode": "soft",
  "steps": [
    {"verify": "davinci.db_snapshot", "as": "before"},
    {"action": "wait", "ms": 500},
    {"verify": "davinci.db_delta", "field": "timeline_video", "baseline": "before", "min_delta": 1}
  ]
}
```

- **`soft`** (default) — log a warning and continue (good for demos; unsaved Resolve projects).
- **`hard`** — raise `ClientError` with code `E_VERIFY` (set `"verify_mode": "hard"` or `CASE_SDK_VERIFY_MODE=hard`).

Built-in verifiers:

| Name | Purpose |
|------|---------|
| `davinci.db_snapshot` | Store row-count baseline (`as` optional) |
| `davinci.db_delta` | Poll until a field grows (or `max_delta` for deletes) |
| `davinci.media_pool_name` | Clip name appears in media pool |
| `davinci.timeline_clips` | Read timeline items (`min_clips` optional) |

```bash
case-sdk verify-list
```

## Procedure discovery (Phase 0)

Built-in DaVinci procedures ship with the package. Override or add your own under
``~/.case/procedures/`` (created by ``case-sdk init``).

```bash
case-sdk procedures
case-sdk run davinci/import_and_append --param filepath=/path/to/clip.mov
case-sdk run davinci/append_to_timeline
```

Search path (first match wins): ``$CASE_SDK_PROCEDURES_PATH`` → ``~/.case/procedures`` → ``~/.case-sdk/procedures`` → built-ins.

| Built-in name | Purpose |
|---------------|---------|
| `davinci/focus_resolve` | Activate DaVinci |
| `davinci/import_media` | Import one file (`filepath`, auto `clip_basename`) |
| `davinci/append_to_timeline` | Append selected pool clip (menu click + DB verify) |
| `davinci/append_to_timeline_hotkey` | Append via Fn+F9 fallback |
| `davinci/import_and_append` | Import then append |

## JSON step lists (no Python required)

Write a `.json` procedure and run it like a `.py` file:

```json
{
  "steps": [
    {"action": "open_app", "name": "TextEdit"},
    {"action": "wait", "ms": 500},
    {"action": "type", "text": "hello ${name}"}
  ],
  "return": {"success": true}
}
```

```bash
case-sdk run examples/open_textedit.json --param name=world
```

## CLI

```
case-sdk run <path-or-name> [--key sk_xxx] [--api wss://...] [--param k=v]...
case-sdk ax-tree [app] [--frontmost] [--find] [--role ...] [--title-contains ...]
case-sdk procedures [--json]
case-sdk verify-list
case-sdk init [--key sk_xxx] [--api wss://...] [--force]
case-sdk doctor
case-sdk --version
```

Configuration resolves in order: CLI flag → environment (`CASE_SDK_API_KEY`, `CASE_SDK_API_BASE`) → `~/.case-sdk/config.toml`.

## MCP (agents — Claude Code, etc.)

The **case-mcp** server exposes the full local SDK over MCP stdio. It does not duplicate
automation logic — every tool delegates to `case_sdk` (same as the CLI).

```bash
pip install -e ".[mcp]"
case-sdk doctor

# Claude Code (use your venv python path)
claude mcp add case-sdk -- python -m case_mcp.server
```

| MCP tool | Purpose |
|----------|---------|
| `case_help` | Planner guide + tool catalog |
| `case_doctor` | Permissions and procedure dirs |
| `case_focus_app` | Activate an app before keystrokes |
| `case_ax_tree` | Dump AX hierarchy |
| `case_find_elements` | Search → `element_ref` dicts |
| `case_list_procedures` | Built-in + user procedure names |
| `case_list_verifiers` | JSON verify step names |
| `case_run` | Run a procedure (`params` dict, optional `verify_mode`) |

Example agent flow: `case_list_procedures` → `case_focus_app("DaVinci Resolve")` →
`case_run("davinci/import_and_append", {"filepath": "/abs/path/clip.mov"})`.

Remote streaming procedures (case-api) remain CLI-only for now (`case-sdk run` with `--key`).

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
