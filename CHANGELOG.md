# Changelog

All notable changes to `case-sdk` are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows semantic versioning once it hits 1.0.

## [0.1.0] — Unreleased

First public version.

### Added

- **Protocol** — JSON envelope shared by both drivers (`hello`, `action`, `sensor`, `result`, `error`, `done`, `ping`, `pong`). Public yield types: `Action`, `Sensor`. Public exception types: `ClientError`, `ProcedureError`.
- **LocalDriver** (Mode A) — runs a procedure generator in-process against a local harness. Per-step thread-pool timeout (default 30s, env-overridable via `CASE_SDK_STEP_TIMEOUT`). Harness exceptions get thrown back into the generator as `ClientError` so procedures can `try/except` and retry. Uncaught failures surface as `ProcedureError`.
- **RemoteDriver** — streams a procedure from a remote WebSocket server. Bearer-token auth on upgrade. Server-driven action/sensor ping-pong. Per-step timeout, per-session idempotency cache, exponential reconnect backoff (1, 2, 4, 8, 30s capped) on transport drops with a fresh session-id per attempt.
- **Harness** — registry of action + sensor primitives. Ten actions (`open_app`, `click`, `type`, `hotkey`, `scroll`, `mouse_down`, `mouse_up`, `wait`, `ax_press`, `ax_set_value`) and three sensors (`window_title`, `ax_value`, `screenshot`). Coordinate clamping inside primary-display bounds, pyautogui fail-safe avoidance, atomacos AX integration with `threading.Thread + join(timeout=2.0)` workaround for hang resistance.
- **Perms onboarding** — `check_perms()` detects Accessibility (via `atomacos`), Screen Recording (via a 1×1 screenshot heuristic), Input Monitoring (returns `None` — undetectable from Python on modern macOS). `ensure_perms()` prints a banner with deep-links to System Settings and exits non-zero if anything is missing.
- **CLI** — `case-sdk run <path-or-name>`, `case-sdk init`, `case-sdk doctor`. Settings resolved from CLI flags > env vars > `~/.case-sdk/config.toml`.
- **Mock server** — `tests/mock_server.py` is a tiny FastAPI/WS server hosting a hardcoded procedure. Used to test RemoteDriver end-to-end without depending on a real server.
- **Tests** — 100+ tests across protocol, drivers, harness, sensors, perms, error boundary, idempotency, keepalive, and CLI.

### License

- FSL-1.1-Apache-2.0 (Functional Source License). Source-available; converts to Apache 2.0 two years after each release. Prohibits Competing Use as defined in the FSL.
