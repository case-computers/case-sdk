"""case-mcp — thin MCP adapter over case-sdk (Phase 4).

All automation logic lives in ``case_sdk`` (harness, inspect, verify, discovery).
This module only exposes tools agents can call over MCP stdio.

Install: ``pip install -e ".[mcp]"`` then register::

    claude mcp add case-sdk -- python -m case_mcp.server

Requires macOS Accessibility (and Screen Recording if procedures use screenshot).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

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
Use tools below — do not invent shell commands for GUI work unless the user
asks for terminal-only tasks.

WORKFLOW
1. case_doctor() — optional preflight (permissions + procedure dirs).
2. case_list_procedures() — discover built-in and user procedure names.
3. For UI you cannot describe: case_ax_tree() or case_find_elements() to
   obtain element_ref dicts for JSON procedures (ax_press / ax_set_value).
4. case_focus_app() before any run that drives a specific app (keystrokes go
   to the frontmost app).
5. case_run(procedure, params) — executes a .json or .py procedure locally.

PROCEDURES
- Names look like ``davinci/import_and_append`` (built-ins ship with the SDK).
- User overrides live in ``~/.case/procedures/`` (first match wins).
- Pass params as a dict, e.g. {"filepath": "/abs/path/clip.mov"} — clip_basename
  is auto-derived from filepath when needed.
- JSON procedures support verify steps (Project.db for DaVinci). Default verify
  mode is soft (warn on stderr). Use verify_mode="hard" to fail closed.

BUILT-IN DAVINCI PROCEDURES (require Resolve open + timeline on Edit page)
- davinci/focus_resolve — activate DaVinci
- davinci/import_media — one file (filepath)
- davinci/append_to_timeline — append selected pool clip (menu + DB verify)
- davinci/append_to_timeline_hotkey — Fn+F9 fallback if menus fail
- davinci/import_and_append — import then append

AX AUTHORING
- case_ax_tree(app) — dump hierarchy (bounded). Use frontmost=True if unsure.
- case_find_elements(app, role=, title_contains=) — returns element_ref candidates.

REMOTE MODE
- case_run only executes *local* discovered procedures and file paths.
- Streaming remote procedures use the case-sdk CLI with an API key;
  not exposed here yet.

Call case_help() for the tool catalog and this guide as structured JSON.
"""


def _runtime(*, verify_mode: str | None = None) -> Any:
    from case_sdk import Runtime

    return Runtime(verify_mode=verify_mode)  # type: ignore[arg-type]


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
    ) -> dict[str, Any]:
        """Execute a local procedure by name or filesystem path.

        Args:
            procedure: e.g. "davinci/import_and_append" or "/path/to/steps.json".
            params: kwargs passed to the procedure (filepath, etc.).
            verify_mode: "soft" or "hard" for JSON verify steps; default soft.
            check_permissions: if True, ensure Accessibility before running.
        """
        from case_sdk import ensure_perms
        from case_sdk.protocol import ClientError, ProcedureError
        from case_sdk.discovery import ProcedureNotFound

        if check_permissions:
            ensure_perms()

        run_params = dict(params or {})
        mode = verify_mode if verify_mode in ("soft", "hard") else None
        rt = _runtime(verify_mode=mode)

        try:
            result = rt.execute_file(procedure, **run_params)
            return {
                "success": True,
                "procedure": procedure,
                "params": run_params,
                "result": result,
            }
        except ProcedureNotFound as e:
            return {
                "success": False,
                "procedure": procedure,
                "error": str(e),
                "code": "E_NOT_FOUND",
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
