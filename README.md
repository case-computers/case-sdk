# case-sdk

Dual-mode runtime for desktop GUI automation on macOS.

- **Local mode** — write your own procedures in Python and run them on your machine.
- **Remote mode** — with an API key, stream procedures from a remote WebSocket server. The procedure source never lands on the client's disk.

Both modes share the same `Runtime` API.

**Developers:** see [docs/DEVELOPER.md](docs/DEVELOPER.md) for API contracts, JSON schemas (`docs/schemas/`), procedure authoring, and MCP.

## Install

```bash
pip install case-sdk              # CLI + Python API
pip install "case-sdk[mcp]"       # add the MCP server for Claude Code / agents
```

From source (for contributors):

```bash
git clone https://github.com/daemonlabshq/case-sdk
cd case-sdk
pip install -e ".[dev,mcp]"
```

Grant Accessibility in *System Settings → Privacy & Security → Accessibility*. Then run `case-sdk doctor` to verify.

Screen Recording is only required if a procedure uses the `screenshot` sensor.

## Quickstart

```bash
case-sdk doctor                                      # check permissions + config
case-sdk procedures                                  # list built-in DaVinci procedures
case-sdk run davinci/focus_resolve                   # bring DaVinci frontmost
case-sdk run examples/open_textedit.json --param name=world
```

`case-sdk doctor` reports any missing macOS permissions and deep-links to System Settings. `case-sdk procedures` shows the discoverable catalog (built-ins plus anything under `~/.case/procedures/`).

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

case-api is live at `wss://api.daemonlabs.run`. Get a key at https://daemonlabs.run/pricing. case-sdk speaks a WebSocket envelope protocol (see `case_sdk.protocol`) so the server holds a procedure and streams one step at a time while the client harness executes it locally. To run against your own backend instead, point `api_base` at it.

```python
from case_sdk import Runtime
rt = Runtime(api_key="sk_...", api_base="wss://your-server.example/ws")
rt.execute("add_clip_to_timeline", filepath="/path/to/clip.mov")
```

Or:

```bash
case-sdk init --api wss://your-server.example/ws --key sk_...    # one-time
case-sdk run add_clip_to_timeline --param filepath=/x.mov
```

In remote mode the server holds the procedure. The runtime receives one
action at a time over WebSocket, runs it locally, returns the observation,
and waits for the next instruction.

## Built-in primitives

Actions: `open_app`, `activate_app`, `click`, `click_ref`, `type`, `hotkey`, `scroll`, `mouse_down`, `mouse_up`, `wait`, `ax_press`, `ax_set_value`, `fn_key`, `drag`, `menu_click`.

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

## Procedure discovery

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
| `davinci/nav/go_to_media_page` | Switch to the Media page (Shift+2) |
| `davinci/nav/go_to_cut_page` | Switch to the Cut page (Shift+3) |
| `davinci/nav/go_to_edit_page` | Switch to the Edit page (Shift+4) |
| `davinci/nav/go_to_fusion_page` | Switch to the Fusion page (Shift+5) |
| `davinci/nav/go_to_color_page` | Switch to the Color page (Shift+6) |
| `davinci/nav/go_to_fairlight_page` | Switch to the Fairlight (audio) page (Shift+7) |
| `davinci/nav/go_to_deliver_page` | Switch to the Deliver page (Shift+8) |
| `davinci/open_media_pool` | Edit page + open Media Pool panel |
| `davinci/import_media` | Import one file (`filepath`, auto `clip_basename`) |
| `davinci/import_media_on_edit` | Go to Edit, then import (`filepath`) |
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

Configuration resolves in order: CLI flag → environment (`CASE_SDK_API_KEY`, `CASE_SDK_API_BASE`) → `~/.case-sdk/config.toml` → built-in default (`wss://api.daemonlabs.run`).

For local mock-server dev, override the default:

```bash
export CASE_SDK_API_BASE=ws://localhost:8000
# or
case-sdk run my/proc --api ws://localhost:8000
```

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

Remote streaming procedures remain CLI-only for now (`case-sdk run` with `--key`).

### MCP "Failed to reconnect / ENOENT"

Make sure `case-mcp` is on the venv path used by your MCP client. Re-register with the absolute path: `claude mcp add case-sdk -- /full/path/to/.venv/bin/case-mcp`.

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

## Troubleshooting

### `SSL: CERTIFICATE_VERIFY_FAILED`

case-sdk bundles certifi for `wss://` connections. If it still fires, `pip install -U case-sdk certifi` and verify your Python is built against a working OpenSSL.

## Security model

case-sdk is a desktop automation runtime. Once Accessibility is granted, a procedure can do anything the user can — and that is the design, not a leak.

- **Local `.py` procedures execute arbitrary Python** at the installing user's privilege. Treat them like shell scripts.
- **JSON procedures are not a sandbox.** They reach every harness primitive (`click`, `type`, `menu_click`, `ax_set_value`, `hotkey`, `screenshot`, ...). A JSON procedure can do anything a Python procedure can short of `import`.
- **`menu_click` runs AppleScript**, which can in turn run shell commands. Treat the `script` field as shell-equivalent.
- **Remote mode (`--key` / `Runtime(api_key=...)`) trusts the server unconditionally.** Whatever WebSocket server you point case-sdk at can stream any action — clicks, keystrokes, screenshot requests — to your machine. Only connect to servers you control or pay for.
- **The `screenshot` sensor captures the entire screen by default.** A procedure or server requesting it can see whatever is on your display.

Practical guidance:

- Only run procedures you wrote or trust.
- Keep `~/.case/procedures/` to yourself (`chmod 700`). Anyone with write access there can drive your apps.
- Don't paste secrets into procedure params if the procedure may forward them anywhere.
- Grant Accessibility only while you're actively using case-sdk; revoke when done.

case-sdk is built for trusted automation flows. It is not built to sandbox untrusted code.

## License

Elastic License 2.0 (ELv2). Source-available. You may use, modify, and self-host case-sdk freely. You may not provide it to third parties as a hosted or managed service. See `LICENSE` for the full text.
