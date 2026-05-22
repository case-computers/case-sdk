# case-sdk — developer guide

This document describes the **public surfaces**: Python API, CLI, JSON procedures, and MCP. It is the stability reference for the current release; behavior may evolve until `v0.2.0`.

For install and quick start, see [README.md](../README.md).

---

## Architecture

```
Agent / human
    │
    ├── case-sdk CLI          ──┐
    └── case-mcp (stdio)      ──┼──► case_sdk.Runtime
                                  │         │
                                  │         ├── LocalDriver → Harness (actions/sensors)
                                  │         └── RemoteDriver → wss://api.daemonlabs.run
                                  │
                                  ├── inspect (AX tree, element_ref)
                                  ├── steps (JSON procedures)
                                  ├── verify (post-step checks)
                                  └── discovery (procedure packs)
```

**Rule:** automation logic lives in `case_sdk`. CLI and MCP are thin adapters.

---

## Public Python API

| Symbol | Purpose |
|--------|---------|
| `Runtime()` | Local + optional remote execution |
| `Runtime.execute_file(path, **params)` | Run `.py` generator or `.json` steps |
| `Runtime.execute(name, **params)` | Remote procedure on a WebSocket server (needs `api_key`) |
| `Action(name, **kwargs)` | Yield from Python procedures |
| `Sensor(name, **kwargs)` | Yield from Python procedures |
| `app_tree(app, ...)` | AX hierarchy as JSON |
| `find_elements(app, ...)` | Search → `element_ref` list |
| `list_procedures()` | Discovery catalog |
| `resolve_procedure(name)` | Name → file path |
| `list_verifiers()` | Verify step names |
| `run_verify(name, args, ctx)` | Run one verify step programmatically |

### Runtime options

```python
from case_sdk import Runtime

rt = Runtime(
    api_key=None,           # set for remote execution against a WebSocket server
    verify_mode="soft",     # or "hard" for JSON verify steps
)
result = rt.execute_file("davinci/import_and_append", filepath="/path/clip.mov")
```

Environment: `CASE_SDK_VERIFY_MODE`, `CASE_SDK_PROCEDURES_PATH`, `CASE_SDK_API_KEY`, `CASE_SDK_STEP_TIMEOUT`.

---

## JSON schemas

Machine-readable schemas live under [schemas/](schemas/):

| Schema | File | Used by |
|--------|------|---------|
| **element_ref** | [element_ref.schema.json](schemas/element_ref.schema.json) | `ax_press`, `ax_set_value`, `ax_value` sensor |
| **ax_tree response** | [ax_tree.schema.json](schemas/ax_tree.schema.json) | `app_tree()`, `case-sdk ax-tree` |
| **procedure** | [procedure.schema.json](schemas/procedure.schema.json) | `*.json` step lists |

Validate a procedure file (optional, requires `check-jsonschema` or similar):

```bash
check-jsonschema --schemafile docs/schemas/procedure.schema.json my_procedure.json
```

---

## element_ref

Points at one Accessibility element. Passed as `element_ref` to harness actions.

```json
{
  "app": "DaVinci Resolve",
  "role": "AXButton",
  "title": "Append to End of Timeline"
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `app` | No | Substring match on localized name; default = frontmost |
| `role` | One of role/title/value/path | e.g. `AXButton` |
| `title` | | Exact `AXTitle` |
| `title_contains` | | Case-insensitive substring (e.g. page tab `edit`) |
| `value` | | Exact `AXValue` |
| `path` | | Index list from root, or `[{"role":"AXWindow"}, {"index": 2}]` |

**Authoring workflow:**

1. `case-sdk ax-tree "Your App" --depth 10` or `--find --role AXButton --title-contains Save`
2. Copy `ref` from find results, or build from tree `role` / `title` / `path`
3. Use in JSON: `{"action": "ax_press", "element_ref": {...}}`

**Trust:** prefer **role + title** over index `path` when possible; paths shift when UI layout changes.

---

## AX tree (`app_tree`)

Top-level response shape:

```json
{
  "app": "DaVinci Resolve",
  "pid": 12345,
  "truncated": false,
  "nodes": 120,
  "tree": {
    "role": "AXApplication",
    "title": "DaVinci Resolve",
    "children": [
      {
        "role": "AXWindow",
        "title": "Project",
        "path": [0],
        "children": []
      }
    ]
  }
}
```

CLI:

```bash
case-sdk ax-tree "DaVinci Resolve" --depth 10
case-sdk ax-tree --frontmost --find --role AXButton --title-contains Append
```

Limits: `max_depth` (default 12), `max_children` (80), `max_nodes` (2500). When `truncated` is true, increase limits or narrow with `--roles`.

---

## JSON procedures

**Format:** JSON only.

Minimal procedure:

```json
{
  "description": "Optional catalog line for case-sdk procedures",
  "verify_mode": "soft",
  "steps": [
    {"action": "activate_app", "name": "DaVinci Resolve", "settle_ms": 700},
    {"action": "wait", "ms": 500},
    {"sensor": "window_title", "save_as": "title"}
  ],
  "return": {"success": true, "title": "${title}"}
}
```

### Step types

| Key | Example | Notes |
|-----|---------|--------|
| `action` | `{"action": "click", "x": 10, "y": 20}` | Any registered harness action |
| `sensor` | `{"sensor": "window_title", "save_as": "t"}` | Observation; optional `save_as` for `${t}` |
| `verify` | `{"verify": "davinci.db_delta", "field": "timeline_video", ...}` | See verify-list |

Params: `${param_name}` in strings, filled from `execute_file(..., **params)`. `filepath` auto-sets `clip_basename`.

### Built-in harness actions (common)

| Action | Args |
|--------|------|
| `activate_app` | `name`, `settle_ms` |
| `open_app` | `name` |
| `click` | `x`, `y`, `button`, `clicks` |
| `type` | `text`, `interval` |
| `hotkey` | `combo` (list) |
| `wait` | `ms` |
| `fn_key` | `key` (f1–f15, Fn modifier) |
| `drag` | `start_x`, `start_y`, `end_x`, `end_y`, `duration_ms` |
| `menu_click` | `script` (AppleScript body) |
| `ax_press` | `element_ref` (AXPress — may no-op on some Qt controls) |
| `click_ref` | `element_ref` (real mouse at element centre) |
| `ax_set_value` | `element_ref`, `value` |
| `scroll`, `mouse_down`, `mouse_up` | see harness |

Sensors: `window_title`, `ax_value`, `screenshot`.

### Procedure packs

| Location | Precedence |
|----------|------------|
| `$CASE_SDK_PROCEDURES_PATH` (colon-separated dirs) | First |
| `~/.case/procedures/` | |
| `~/.case-sdk/procedures/` | |
| `case_sdk/builtin_procedures/` (shipped) | Last |

```bash
case-sdk procedures
case-sdk run davinci/import_and_append --param filepath=/abs/path/clip.mov
```

Shipped built-ins include `davinci/nav/go_to_<page>_page` (media, cut, edit, fusion, color, fairlight, deliver), `davinci/open_media_pool`, `davinci/import_media_on_edit`, `davinci/import_media`, `davinci/append_to_timeline`, `davinci/import_and_append`, and others (see `case-sdk procedures`).

---

## User-action-required procedures

A procedure can declare it needs the user to do something in the target app
before steps run (for example, select a clip in DaVinci's Media Pool).

```json
{
  "description": "...",
  "requires_user_action": true,
  "user_action_prompt": "In DaVinci's Media Pool, click the clip you want to append. Then continue.",
  "steps": [ ... ]
}
```

**CLI:** `case-sdk run` prints the prompt and waits for Enter before executing.
Pass `--yes` (or `-y`) to skip the prompt in scripts / CI. If stdin is not a TTY
and `--yes` is not passed, the run aborts with exit code 130.

**MCP:** `case_list_procedures` surfaces `requires_user_action` and
`user_action_prompt` per entry. Agents must show the prompt to the user, wait
for confirmation, then call:

```json
case_run({
  "procedure": "davinci/append_to_timeline",
  "params": {},
  "user_action_confirmed": true
})
```

Without `user_action_confirmed`, `case_run` returns
`{success: false, code: "E_USER_ACTION_REQUIRED", prompt: "..."}` — relay the
prompt to the user, then retry with the flag set.

Only JSON procedures support this declaration; `.py` generators handle their
own user-interaction logic.

---

## Python procedures

```python
from case_sdk import Action, Sensor, ClientError

def procedure(params):
    yield Action("open_app", name="TextEdit")
    yield Action("wait", ms=500)
    title = yield Sensor("window_title")
    return {"success": True, "title": title.get("value")}
```

Same `LocalDriver` loop as JSON steps. Use when you need branches, loops, or `try/except ClientError`.

---

## Verification

Verify steps run **inside** JSON procedures. List names:

```bash
case-sdk verify-list
```

DaVinci / Project.db verifiers: `davinci.db_snapshot`, `davinci.db_delta`, `davinci.media_pool_name`, `davinci.timeline_clips`.

```json
{"verify": "davinci.db_snapshot", "as": "before"},
{"verify": "davinci.db_delta", "field": "timeline_video", "baseline": "before", "min_delta": 1, "timeout": 5}
```

- **soft** — log warning, continue (default; good for unsaved Resolve projects).
- **hard** — `ClientError` code `E_VERIFY`.

---

## Remote mode — case-api

| Endpoint | Auth | Purpose |
|---|---|---|
| `https://api.daemonlabs.run/health` | none | health probe |
| `https://api.daemonlabs.run/v1/procedures` | Bearer | list procedures available for your key |
| `wss://api.daemonlabs.run/ws` | Bearer | procedure execution stream |

case-api is live: `https://api.daemonlabs.run` (HTTPS) and `wss://api.daemonlabs.run/ws` (WebSocket). Get a key from https://daemonlabs.run/pricing, export it as `CASE_API_KEY`, the runtime handles the rest.

The wire protocol is open. If you want to run your own backend, `tests/mock_server.py` is the ~250-line reference. Anything built against case-sdk works against either.

---

## MCP

`case_mcp` is a thin stdio server that delegates every tool to `case_sdk`. No automation logic lives in MCP.

```bash
pip install -e ".[mcp]"
claude mcp add case-sdk -- python -m case_mcp.server
```

Tools: `case_help`, `case_doctor`, `case_focus_app`, `case_ax_tree`, `case_find_elements`, `case_list_procedures`, `case_list_verifiers`, `case_run`.

An agent can run `case_run("davinci/import_and_append", {"filepath": "..."})` for full local execution.

---

## Deferred

| Item | Notes |
|------|-------|
| `case-sdk record` (click → procedure file) | Not yet implemented |
| YAML procedures | JSON only for now |
| Generic / AX verify steps | DaVinci DB verifiers shipped; broader verify TBD |
| Remote procedures over MCP | Use the CLI with an API key for now |

---

## Contributing procedures

1. Add `my_pack/my_flow.json` under `~/.case/procedures/` (or ship under `case_sdk/builtin_procedures/`).
2. Include `"description"` for the catalog.
3. Run `case-sdk run my_pack/my_flow --param ...`.
4. Validate against [procedure.schema.json](schemas/procedure.schema.json).

For DaVinci, ensure Resolve is frontmost, Edit page, and a timeline exists before append/import flows.
