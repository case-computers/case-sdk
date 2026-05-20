# case-sdk — developer guide

This document describes the **public surfaces** on `main`: Python API, CLI, JSON procedures, and MCP. It is the stability reference for Phase 0–1; behavior may evolve until we tag `v0.2.0`.

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
                                  │         └── RemoteDriver → case-api (optional)
                                  │
                                  ├── inspect (AX tree, element_ref)
                                  ├── steps (JSON procedures)
                                  ├── verify (post-step checks)
                                  └── discovery (procedure packs)
```

**Rule:** automation logic lives in `case_sdk`. CLI and MCP are thin adapters.

**Not on `main`:** `case_mcp_eh` (see [MCP vs davinci-demo](#mcp-on-main-vs-davinci-demo-branch) below).

---

## Public Python API

| Symbol | Purpose |
|--------|---------|
| `Runtime()` | Local + optional remote execution |
| `Runtime.execute_file(path, **params)` | Run `.py` generator or `.json` steps |
| `Runtime.execute(name, **params)` | Remote procedure (needs `api_key`) |
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
    api_key=None,           # set for remote Mode B
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

**Authoring workflow (recorder deferred):**

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

## JSON procedures (Phase 2)

**Format:** JSON only on `main` (YAML deferred).

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

**Parity:** more DaVinci flows will be added as JSON examples (2–3 at a time) from the execution engine reference; not a full port of every EH procedure.

Shipped built-ins include `davinci/go_to_page`, `davinci/open_media_pool`, `davinci/import_media_on_edit`, `davinci/import_media`, `davinci/append_to_timeline`, `davinci/import_and_append`, and others (see `case-sdk procedures`).

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

## Verification (Phase 3)

Verify steps run **inside** JSON procedures (not in MCP prompts). List names:

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

Additional verify types (generic DB, AX state, custom hooks) are **not decided yet** — pending product choice.

---

## MCP on main vs davinci-demo branch

### What you have on **main** (complete for this repo’s SDK)

| Piece | Role |
|-------|------|
| `case_sdk` | **All execution logic**: harness, drivers, inspect, steps, verify, discovery, built-in JSON procedures |
| `case_mcp` | **Thin MCP** (stdio) that calls `case_sdk` — same as CLI, not a second engine |

Tools: `case_help`, `case_doctor`, `case_focus_app`, `case_ax_tree`, `case_find_elements`, `case_list_procedures`, `case_list_verifiers`, `case_run`.

```bash
pip install -e ".[mcp]"
claude mcp add case-sdk -- python -m case_mcp.server
```

An agent on `main` can run `case_run("davinci/import_and_append", {"filepath": "..."})` with **full execution** — no EmulatedHumanoid repo required.

### What **`case_mcp_eh`** is (only on **davinci-demo** / `deadline` checkout)

A **different, older MCP server** that:

- Imports **`resolve_agent.py` from EmulatedHumanoid** (external repo, ~69 procedures, EH pre/postconditions).
- Exposes only `davinci_list_procedures`, `davinci_call`, `davinci_focus`.
- Does **not** use the new `case_sdk` JSON/discovery/verify stack on `main`.

We **did not** put `case_mcp_eh` on `main` because:

1. You already have a **full working MCP on `main`** (`case_mcp`) for everything built here.
2. `case_mcp_eh` would drag in EH as a required dependency and duplicate the world you are replacing with JSON + `case_sdk`.
3. You wanted to avoid merging `davinci-demo` into `main` and avoid git conflicts.

**“Shim on main ❌”** means: we did not copy that EH wrapper to `main`. It is **not** that MCP on `main` is incomplete — it means the **EH bridge** stays on the other branch until you explicitly want EH’s 69 procedures callable via MCP without porting them to JSON.

If you later need one EH procedure before JSON parity, options are:

- Port that procedure to `builtin_procedures/davinci/*.json`, or
- Add an optional `case_mcp_eh` extra on `main` that depends on `$EH_REPO_PATH` (compat only).

---

## case-api, paid library, and multiple harnesses

**Direction (your decision):**

- **Free users:** local built-ins + `~/.case/procedures` (what ships in the wheel).
- **Paid users:** procedures served by **case-api** (Mode B) — catalog in your DB (Supabase/Postgres is fine); client never stores procedure source.
- **Not production:** shipping all 69 EH Python procedures inside the client via `case_mcp_eh`.

**Multiple harnesses:** the wire protocol is already **primitive names + args** (`action` / `sensor` envelopes). A case-api procedure is ideally stored as **JSON steps** (or compiled to steps server-side). The **client** registers whichever harness implements those names:

| Client harness | Examples |
|----------------|----------|
| `case_sdk` (default) | pyautogui, AX, DaVinci menus |
| Future / plugin | browser CDP, another app’s bridge |

Paid API usage = `api_key` + allowed procedure names + optional `harness_profile` in procedure metadata so the server only emits actions the client understands. EH-specific logic stays **server-side** until ported to JSON — not required in the pip package.

**69 procedures:** decide per procedure whether to port to JSON (local/free tier), host on case-api (paid), or drop. No need to copy EH into Supabase as Python.

---

## Deferred / TBD

| Item | Phase | Status |
|------|-------|--------|
| `case-sdk record` (click → procedure file) | 1 | Deferred |
| YAML procedures | 2 | JSON only |
| More DaVinci JSON examples | 2 | 2–3 at a time when you supply reference flows |
| Generic / AX verify | 3 | You will decide |
| Remote procedures over MCP | 4 | CLI only today |
| Pack-loaded MCP instructions | 4 | Planner text still in `server.py`; can move to pack README later |

---

## Contributing procedures

1. Add `my_pack/my_flow.json` under `~/.case/procedures/` (or ship under `case_sdk/builtin_procedures/`).
2. Include `"description"` for the catalog.
3. Run `case-sdk run my_pack/my_flow --param ...`.
4. Validate against [procedure.schema.json](schemas/procedure.schema.json).

For DaVinci, ensure Resolve is frontmost, Edit page, and a timeline exists before append/import flows.
