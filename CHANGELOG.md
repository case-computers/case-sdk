# Changelog

All notable changes to `case-sdk` are documented here.

## [0.1.3]

### Added

- `case_mcp` falls through to remote dispatch when `case_run` is called with a procedure not found locally; only one step at a time streams over WS, full body stays on the server.
- New MCP tool `case_list_remote_procedures` hits the server's `/v1/procedures` HTTP catalog (requires `api_key` + `api_base` in config or env).
- `RemoteDriver` now configures its own `SSLContext` rooted at bundled `certifi` for `wss://` URLs; removes reliance on the system trust store (fixes `[SSL: CERTIFICATE_VERIFY_FAILED]` on python.org / pyenv builds). `ws://` unchanged.

### Changed (BREAKING)

- Removed bundled `davinci/go_to_page.json` (its `click_ref(AXCheckBox)` selector no longer resolves on current Resolve). Replaced by 7 hotkey-based procedures under `davinci/nav/`: `go_to_{media,cut,edit,fusion,color,fairlight,deliver}_page`, each firing `Shift+N` (N=2..8). Names match the case-api server seeds.
- Default `api_base` is now `wss://api.daemonlabs.run` (was `ws://localhost:8000`). Affects `Runtime`, `RemoteDriver`, the CLI, and `case_mcp`. Mock-server users: set `CASE_SDK_API_BASE=ws://localhost:8000` or pass `--api ws://localhost:8000`.

### Fixed

- macOS-only deps (`pyautogui`, `atomacos`, `Pillow`) now gated by `sys_platform == "darwin"` so the package installs cleanly on Linux (Cloud Run, CI).

### Security

- `pydantic` floor lifted to `>=2.4` and `Pillow` floor to `>=11.3` (closes CVE-2024-3772, CVE-2024-28219, CVE-2025-48379).

## [0.1.2]

### Added

- Internal harness extension namespace (`case_sdk.apps`) for app-specific
  bundles. Bundles attach to `build_default_harness()` under
  `try/except ImportError` so non-macOS consumers keep working unchanged.
- `element_ref` resolution now accepts optional `description` and
  `description_contains` keys for matching by ``AXDescription``. Useful for
  icon-only buttons whose `AXTitle` is empty.

### Fixed

- `_run_verify_safe` in the remote driver now catches every exception class
  (not just `VerifyError`) and surfaces them as `{ok: false, reason: ...}`
  so a missing local prerequisite (e.g. ``FileNotFoundError`` from
  `davinci.db_*` on a Mac without DaVinci installed) no longer crashes the
  client mid-procedure.

## [0.1.1]

### Added

- **User-action-required procedures.** JSON procedures may declare
  `requires_user_action: true` plus `user_action_prompt` to signal that the
  user needs to do something in the target app first (e.g. select a clip).
  `case-sdk run` shows the prompt and waits for Enter; pass `--yes` to skip
  in scripts. `case_mcp` surfaces the fields via `case_list_procedures`; the
  MCP `case_run` tool refuses to execute such procedures unless the agent
  passes `user_action_confirmed=true`, returning the structured error
  `{success: false, code: "E_USER_ACTION_REQUIRED", prompt: "..."}`.
- Built-in `davinci/append_to_timeline` and `davinci/append_to_timeline_hotkey`
  now declare `requires_user_action: true` so agents and the CLI ask the
  user to select a pool clip before firing the append.

### Fixed

- `case_ax_tree` (and `find_elements` / `case-sdk ax-tree`) no longer
  resolves to overlay services like `QuickLookUIService` when the caller
  passes a real app name (e.g. `TextEdit`). Resolution now prefers exact
  `localizedName`, then `bundleIdentifier`, then `com.apple.<name>`,
  before falling back to substring matching with a helper-services
  deny-list.

### Security

- WebSocket frame size pinned to 1 MB explicitly so a misbehaving remote
  server cannot stream oversized payloads.
- `case-sdk init` now writes `~/.case-sdk/config.toml` with mode `0600`
  (and the directory with `0700`) so the API key is not readable by
  other processes on the same machine.
- Procedure discovery refuses to follow symlinks whose target escapes
  the search root (e.g. `~/.case/procedures/x -> /etc/passwd`).
- `list_procedures()` emits a `RuntimeWarning` when a user-dir procedure
  shadows a built-in of the same qualified name. First-match precedence
  is unchanged for 0.1.1.
- `${param}` substitution into a `menu_click.script` emits a
  `RuntimeWarning` when it introduces AppleScript shell-exec patterns
  (`do shell script`, backticks). The step still runs.
- Passing `db_path` to `davinci.db_*` verify steps is now deprecated and
  will be removed in 0.2.0. Use `project_hint` or let the heuristic
  pick the active Project.db.
- `pydantic` floor lifted to `>=2.4` (CVE-2024-3772).
- Explicit `Pillow>=11.3` dependency (CVE-2024-28219, CVE-2025-48379).
- Third-party GitHub Actions in the publish workflow are pinned to
  commit SHAs instead of mutable tags.

### Docs

- New "Security model" section describing the trust boundary
  case-sdk exposes (procedures run as the installing user with
  Accessibility; remote servers receive unconditional trust).

## [0.1.0]

First version.

### Added

- **Protocol** — JSON envelope shared by both drivers (`hello`, `action`, `sensor`, `result`, `error`, `done`, `ping`, `pong`). Public yield types: `Action`, `Sensor`. Public exception types: `ClientError`, `ProcedureError`.
- **LocalDriver** — runs a procedure generator in-process against a local harness. Per-step thread-pool timeout (default 30s, env-overridable via `CASE_SDK_STEP_TIMEOUT`). Harness exceptions get thrown back into the generator as `ClientError` so procedures can `try/except` and retry. Uncaught failures surface as `ProcedureError`.
- **RemoteDriver** — streams a procedure from a remote WebSocket server. Bearer-token auth on upgrade. Server-driven action/sensor ping-pong. Per-step timeout, per-session idempotency cache, exponential reconnect backoff (1, 2, 4, 8, 30s capped) on transport drops with a fresh session-id per attempt.
- **Harness** — registry of action + sensor primitives. Ten actions (`open_app`, `click`, `type`, `hotkey`, `scroll`, `mouse_down`, `mouse_up`, `wait`, `ax_press`, `ax_set_value`) and three sensors (`window_title`, `ax_value`, `screenshot`). Coordinate clamping inside primary-display bounds, pyautogui fail-safe avoidance, atomacos AX integration with `threading.Thread + join(timeout=2.0)` workaround for hang resistance.
- **Perms onboarding** — `check_perms()` detects Accessibility (via `atomacos`), Screen Recording (via a 1×1 screenshot heuristic), Input Monitoring (returns `None` — undetectable from Python on modern macOS). `ensure_perms()` prints a banner with deep-links to System Settings and exits non-zero if anything is missing.
- **CLI** — `case-sdk run <path-or-name>`, `case-sdk init`, `case-sdk doctor`. Settings resolved from CLI flags > env vars > `~/.case-sdk/config.toml`.
- **Mock server** — `tests/mock_server.py` is a tiny FastAPI/WS server hosting a hardcoded procedure. Used to test RemoteDriver end-to-end without depending on a real server.
- **Tests** — 100+ tests across protocol, drivers, harness, sensors, perms, error boundary, idempotency, keepalive, and CLI.

### License

- Elastic License 2.0 (ELv2). Source-available; permanent restriction on offering case-sdk as a hosted or managed service to third parties. All other use is permitted. See `LICENSE`.
