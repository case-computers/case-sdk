"""case-mcp — thin MCP adapter over case-sdk.

All automation logic lives in ``case_sdk`` (harness, inspect, verify, discovery).
This module only exposes tools agents can call over MCP stdio.

Install: ``pip install -e ".[mcp]"`` then register::

    claude mcp add case-sdk -- python -m case_mcp.server

Requires macOS Accessibility (and Screen Recording if procedures use screenshot).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_FILE = Path.home() / ".case-sdk" / "config.toml"

# MCP uses stdout for JSON-RPC; log to stderr only.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("case-mcp")

INSTRUCTIONS = """\
You control macOS desktop apps through case-sdk via these MCP tools.

DEFAULT BIAS: ACT on clear action verbs (open, click, type, import, run, append).
Use the tools below — do not invent shell commands for GUI work.

WORKFLOW
1. case_doctor() — optional preflight (permissions + procedure dirs).
2. case_list_procedures() — discover built-in (e.g. ``davinci/*``) and user
   procedure names.
3. case_focus_app(name) before any run that drives a specific app
   (keystrokes go to the frontmost app).
4. case_run(procedure, params) — execute a procedure locally; falls through
   to the server when the procedure isn't on disk and an api_key is set.

If a procedure declares ``requires_user_action`` in its JSON, case_run
returns ``code: "E_USER_ACTION_REQUIRED"`` until you re-call with
``user_action_confirmed=true`` after the user has done the manual step.

Call case_help() for the tool catalog and this guide as structured JSON.
"""


def _load_remote_config() -> dict[str, str]:
    """Read api_key + api_base from ~/.case-sdk/config.toml + env vars."""
    cfg: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as f:
            cfg = tomllib.load(f).get("default", {})
    api_key = os.environ.get("CASE_SDK_API_KEY") or cfg.get("api_key", "")
    api_base = os.environ.get("CASE_SDK_API_BASE") or cfg.get(
        "api_base", "wss://api.daemonlabs.run"
    )
    return {"api_key": api_key, "api_base": api_base}


def _http_base(ws_base: str) -> str:
    """Convert wss://… → https://…, ws://… → http://… for HTTP endpoints."""
    if ws_base.startswith("wss://"):
        return "https://" + ws_base[len("wss://") :]
    if ws_base.startswith("ws://"):
        return "http://" + ws_base[len("ws://") :]
    return ws_base


def _runtime(*, verify_mode: str | None = None, remote: bool = False) -> Any:
    """Build a Runtime. `remote=True` wires the api_key + api_base from config
    so rt.execute() can dispatch to the WS server when a procedure isn't local."""
    from case_sdk import Runtime

    if not remote:
        return Runtime(verify_mode=verify_mode)  # type: ignore[arg-type]
    cfg = _load_remote_config()
    return Runtime(
        api_key=cfg["api_key"],
        api_base=cfg["api_base"],
        verify_mode=verify_mode,
    )  # type: ignore[arg-type]


def create_mcp():
    """Build the FastMCP instance (requires ``mcp`` package)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("case-sdk", instructions=INSTRUCTIONS)

    @mcp.tool()
    def case_help() -> dict[str, Any]:
        """Return planner instructions and the tool catalog (structured).

        Call once per session if you need a refresher on workflows, DaVinci
        built-ins, or available tools.
        """
        tools: list[dict[str, Any]] = []
        for tool in mcp._tool_manager._tools.values():
            tools.append(
                {
                    "name": tool.name,
                    "description": (tool.description or "").strip(),
                    "parameters": tool.parameters,
                }
            )
        return {
            "planner_guide": INSTRUCTIONS,
            "tools": sorted(tools, key=lambda t: t["name"]),
        }

    @mcp.tool()
    def case_doctor() -> dict[str, Any]:
        """Check macOS permissions and procedure search paths (read-only).

        Does not run automation or modify system state.
        """
        from case_sdk import check_perms, list_procedures
        from case_sdk.discovery import search_paths, default_procedures_dir
        from case_sdk.perms import missing

        status = check_perms()
        miss = missing(status)
        return {
            "success": len(miss) == 0,
            "permissions": {
                "accessibility": status.accessibility,
                "screen_recording": status.screen_recording,
                "input_monitoring": status.input_monitoring,
            },
            "missing": miss,
            "procedure_dirs": [str(p) for p in search_paths()],
            "user_procedures_dir": str(default_procedures_dir()),
            "procedure_count": len(list_procedures()),
        }

    @mcp.tool()
    def case_focus_app(name: str, settle_ms: int = 700) -> dict[str, Any]:
        """Bring an application frontmost (osascript activate + settle wait).

        Args:
            name: localized app name, e.g. "DaVinci Resolve".
            settle_ms: milliseconds to wait after activate before returning.
        """
        rt = _runtime()
        return rt.harness.execute_action(
            "activate_app", {"name": name, "settle_ms": int(settle_ms)}
        )

    @mcp.tool()
    def case_ax_tree(
        app: str | None = None,
        frontmost: bool = False,
        max_depth: int = 12,
        max_children: int = 80,
        max_nodes: int = 2500,
        roles: str | None = None,
    ) -> dict[str, Any]:
        """Dump an accessibility tree as JSON for procedure authoring.

        Args:
            app: app name substring; omit when frontmost=True.
            frontmost: inspect the frontmost app instead of ``app``.
            max_depth: stop recursing after this depth.
            max_children: cap children per node.
            max_nodes: global node budget.
            roles: optional comma-separated AXRole filter (e.g. "AXButton,AXTextField").
        """
        from case_sdk import InspectError, app_tree

        role_list = None
        if roles:
            role_list = [r.strip() for r in roles.split(",") if r.strip()]
        try:
            return app_tree(
                app,
                frontmost=frontmost or not app,
                max_depth=int(max_depth),
                max_children=int(max_children),
                max_nodes=int(max_nodes),
                roles=role_list,
            )
        except InspectError as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def case_find_elements(
        app: str | None = None,
        frontmost: bool = False,
        role: str | None = None,
        title: str | None = None,
        title_contains: str | None = None,
        max_results: int = 20,
        max_depth: int = 12,
    ) -> dict[str, Any]:
        """Search an AX tree and return element_ref dicts for matching elements.

        Use refs in JSON procedures with ax_press / ax_set_value actions.
        """
        from case_sdk import InspectError, find_elements

        try:
            matches = find_elements(
                app,
                frontmost=frontmost or not app,
                role=role,
                title=title,
                title_contains=title_contains,
                max_results=int(max_results),
                max_depth=int(max_depth),
            )
            return {"success": True, "matches": matches, "count": len(matches)}
        except InspectError as e:
            return {"success": False, "error": str(e), "matches": []}

    @mcp.tool()
    def case_list_procedures() -> dict[str, Any]:
        """List discoverable local procedures (.json and .py)."""
        from case_sdk import list_procedures

        entries = list_procedures()
        return {"success": True, "count": len(entries), "procedures": entries}

    @mcp.tool()
    def case_list_remote_procedures() -> dict[str, Any]:
        """Fetch the server's procedure catalog over HTTP (/v1/procedures).

        Requires api_key + api_base in ~/.case-sdk/config.toml or env vars.
        Returns name, version, description, and the requires_user_action flag.
        Catalogs procedures available only on the server (e.g. davinci/color/*)
        that aren't bundled with the local SDK install.
        """
        cfg = _load_remote_config()
        if not cfg["api_key"]:
            return {
                "success": False,
                "error": "no api_key configured — run `case-sdk init --key …`",
                "procedures": [],
            }
        url = _http_base(cfg["api_base"]).rstrip("/") + "/v1/procedures"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {cfg['api_key']}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {
                "success": False,
                "error": f"HTTP {e.code}: {e.reason}",
                "url": url,
                "procedures": [],
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
                "url": url,
                "procedures": [],
            }
        procs = data.get("procedures", []) if isinstance(data, dict) else []
        return {
            "success": True,
            "count": len(procs),
            "procedures": procs,
            "source": url,
        }

    @mcp.tool()
    def case_list_verifiers() -> dict[str, Any]:
        """List verify step names usable inside JSON procedures."""
        from case_sdk.verify import list_verifiers

        names = list_verifiers()
        return {"success": True, "count": len(names), "verifiers": names}

    @mcp.tool()
    def case_run(
        procedure: str,
        params: dict[str, Any] | None = None,
        verify_mode: str | None = None,
        check_permissions: bool = True,
        user_action_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Execute a local procedure by name or filesystem path.

        Args:
            procedure: e.g. "davinci/import_and_append" or "/path/to/steps.json".
            params: kwargs passed to the procedure (filepath, etc.).
            verify_mode: "soft" or "hard" for JSON verify steps; default soft.
            check_permissions: if True, ensure Accessibility before running.
            user_action_confirmed: pass True after the user has performed the
                manual action required by procedures that declare
                ``requires_user_action`` in their JSON. Without confirmation
                such procedures fail fast with code ``E_USER_ACTION_REQUIRED``.
        """
        from case_sdk import ensure_perms
        from case_sdk.protocol import ClientError, ProcedureError
        from case_sdk.discovery import (
            ProcedureNotFound,
            read_procedure_metadata,
            resolve_procedure,
        )

        if check_permissions:
            ensure_perms()

        run_params = dict(params or {})
        mode = verify_mode if verify_mode in ("soft", "hard") else None
        rt = _runtime(verify_mode=mode)

        try:
            resolved = resolve_procedure(procedure)
        except ProcedureNotFound:
            # Fall through to remote dispatch if an API key is configured.
            # The full procedure body never leaves the server — only one step
            # at a time streams over WS for the client harness to execute.
            cfg = _load_remote_config()
            if not cfg["api_key"]:
                return {
                    "success": False,
                    "procedure": procedure,
                    "error": (
                        f"procedure {procedure!r} not found locally and no "
                        "api_key configured for remote dispatch"
                    ),
                    "code": "E_NOT_FOUND",
                }
            rt_remote = _runtime(verify_mode=mode, remote=True)
            try:
                result = rt_remote.execute(procedure, **run_params)
                return {
                    "success": True,
                    "procedure": procedure,
                    "params": run_params,
                    "result": result,
                    "mode": "remote",
                }
            except (ClientError, ProcedureError) as e:
                return {
                    "success": False,
                    "procedure": procedure,
                    "params": run_params,
                    "error": str(e),
                    "code": getattr(e, "code", "E_PROCEDURE"),
                    "mode": "remote",
                }
            except Exception as e:
                log.exception("case_run remote dispatch failed")
                return {
                    "success": False,
                    "procedure": procedure,
                    "params": run_params,
                    "error": f"{type(e).__name__}: {e}",
                    "code": "E_RUNTIME",
                    "mode": "remote",
                }

        meta = read_procedure_metadata(resolved)
        if meta.get("requires_user_action") and not user_action_confirmed:
            prompt = meta.get(
                "user_action_prompt",
                "Manual setup needed in the target app before continuing.",
            )
            return {
                "success": False,
                "procedure": procedure,
                "code": "E_USER_ACTION_REQUIRED",
                "error": f"User action required: {prompt}",
                "prompt": prompt,
            }

        try:
            result = rt.execute_file(procedure, **run_params)
            return {
                "success": True,
                "procedure": procedure,
                "params": run_params,
                "result": result,
            }
        except (ClientError, ProcedureError) as e:
            return {
                "success": False,
                "procedure": procedure,
                "params": run_params,
                "error": str(e),
                "code": getattr(e, "code", "E_PROCEDURE"),
            }
        except Exception as e:
            log.exception("case_run failed")
            return {
                "success": False,
                "procedure": procedure,
                "params": run_params,
                "error": f"{type(e).__name__}: {e}",
                "code": "E_RUNTIME",
            }

    return mcp


def main() -> None:
    try:
        mcp = create_mcp()
    except ImportError as e:
        print(
            "case-mcp requires the mcp package: pip install -e '.[mcp]'",
            file=sys.stderr,
        )
        raise SystemExit(1) from e
    log.info("case-mcp starting (stdio transport)")
    mcp.run()


if __name__ == "__main__":
    main()
