"""case-sdk command-line interface.

Subcommands:
  - ``run <path-or-name>``  execute a .py or .json procedure (Mode A) or remote name
  - ``ax-tree [app]``       dump AX tree or search for element refs
  - ``init``                write ``~/.case-sdk/config.toml``
  - ``doctor``              check perms, config, basic readiness

Mode A vs Mode B is decided by whether ``target`` resolves to an existing
file on disk — keeps the CLI ergonomic for both flows without a flag.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from case_sdk import (
    Runtime,
    __version__,
    app_tree,
    check_perms,
    ensure_perms,
    list_procedures,
)
from case_sdk.discovery import ProcedureNotFound, default_procedures_dir
from case_sdk.inspect import InspectError, find_elements
from case_sdk.verify import list_verifiers
from case_sdk.perms import missing, print_onboarding_banner

CONFIG_DIR = Path.home() / ".case-sdk"
CONFIG_FILE = CONFIG_DIR / "config.toml"


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    with CONFIG_FILE.open("rb") as f:
        data = tomllib.load(f)
    return data.get("default", {})


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config()
    api_key = args.key or os.environ.get("CASE_SDK_API_KEY") or cfg.get("api_key")
    api_base = (
        args.api
        or os.environ.get("CASE_SDK_API_BASE")
        or cfg.get("api_base", "ws://localhost:8000")
    )

    params: dict[str, Any] = {}
    for kv in args.params or []:
        if "=" not in kv:
            print(f"error: --param must be k=v, got {kv!r}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        params[k] = v

    target_path = Path(args.target).expanduser()
    is_file = target_path.is_file()
    rt = Runtime(
        api_key=api_key,
        api_base=api_base,
        verify_mode=args.verify_mode or None,
    )

    # Local: explicit file path, or a name resolved via procedure discovery.
    run_local = is_file
    if not run_local:
        try:
            from case_sdk.discovery import resolve_procedure

            resolve_procedure(args.target)
            run_local = True
        except ProcedureNotFound:
            run_local = False

    if run_local:
        if not args.skip_perm_check:
            ensure_perms()
        try:
            result = rt.execute_file(args.target, **params)
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    else:
        if not api_key:
            print(
                "error: unknown local procedure and no API key for remote mode.\n"
                "  Use a file path, a name from `case-sdk procedures`, or pass --key.",
                file=sys.stderr,
            )
            return 2
        try:
            result = rt.execute(args.target, **params)
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    print(json.dumps(result, indent=2))
    return 0


def cmd_procedures(args: argparse.Namespace) -> int:
    entries = list_procedures()
    if args.json:
        print(json.dumps({"procedures": entries}, indent=2))
        return 0
    if not entries:
        print("No procedures found.")
        print(f"  Add .json or .py files under {default_procedures_dir()}/")
        return 0
    print(f"{'NAME':<40} {'KIND':<6} DESCRIPTION")
    print("-" * 72)
    for e in entries:
        desc = e.get("description", "")[:28]
        print(f"{e['name']:<40} {e['kind']:<6} {desc}")
    print()
    print("Run: case-sdk run <name> [--param k=v]")
    print(f"User procedures dir: {default_procedures_dir()}/")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    proc_dir = default_procedures_dir()
    proc_dir.mkdir(parents=True, exist_ok=True)
    readme = proc_dir / "README.md"
    if not readme.exists() or args.force:
        readme.write_text(
            "# case-sdk procedures\n\n"
            "Place `.json` step lists or `.py` generator procedures here.\n\n"
            "Example:\n\n"
            "  case-sdk run davinci/import_and_append "
            "--param filepath=/path/to/clip.mov\n\n"
            "Built-in procedures ship inside the case-sdk package under "
            "`davinci/` — copy and edit files here to override them.\n",
            encoding="utf-8",
        )
    if CONFIG_FILE.exists() and not args.force:
        print(
            f"config exists at {CONFIG_FILE} — use --force to overwrite",
            file=sys.stderr,
        )
        return 1

    api_key = args.key
    if api_key is None:
        try:
            api_key = input("API key (blank for local-only mode): ").strip()
        except EOFError:
            api_key = ""

    api_base = args.api or "wss://api.case.dev"

    lines = ["[default]"]
    if api_key:
        lines.append(f'api_key = "{api_key}"')
    lines.append(f'api_base = "{api_base}"')
    CONFIG_FILE.write_text("\n".join(lines) + "\n")
    print(f"wrote {CONFIG_FILE}")
    return 0


def cmd_verify_list(args: argparse.Namespace) -> int:
    print(json.dumps({"verifiers": list_verifiers()}, indent=2))
    return 0


def cmd_ax_tree(args: argparse.Namespace) -> int:
    if not args.skip_perm_check:
        ensure_perms()
    frontmost = args.frontmost or not args.app
    roles = None
    if args.roles:
        roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    try:
        if args.find:
            results = find_elements(
                args.app,
                frontmost=frontmost,
                role=args.role,
                title=args.title,
                title_contains=args.title_contains,
                max_results=args.limit,
                max_depth=args.depth,
            )
            print(json.dumps({"matches": results}, indent=2))
        else:
            tree = app_tree(
                args.app,
                frontmost=frontmost,
                max_depth=args.depth,
                max_children=args.max_children,
                max_nodes=args.max_nodes,
                roles=roles,
            )
            print(json.dumps(tree, indent=2))
    except InspectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"case-sdk {__version__}")
    print()

    status = check_perms()
    print("permissions:")
    print(f"  accessibility:    {_mark(status.accessibility)}")
    print(f"  screen recording: {_mark(status.screen_recording)}")
    print(f"  input monitoring: {_mark(status.input_monitoring)}")

    missing_perms = missing(status)
    if missing_perms:
        print()
        print_onboarding_banner(missing_perms)

    print()
    print("config:")
    if CONFIG_FILE.exists():
        cfg = load_config()
        print(f"  file:     {CONFIG_FILE}")
        print(f"  api_base: {cfg.get('api_base', '(not set)')}")
        print(f"  api_key:  {'set' if cfg.get('api_key') else 'not set'}")
    else:
        print(f"  no config — run `case-sdk init`")

    return 1 if missing_perms else 0


def _mark(v: bool | None) -> str:
    if v is None:
        return "? (undetectable)"
    return "ok" if v else "missing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="case-sdk", description="case-sdk CLI")
    parser.add_argument(
        "--version", action="version", version=f"case-sdk {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="execute a procedure")
    p_run.add_argument(
        "target",
        help="local .py procedure, .json step list, or remote procedure name",
    )
    p_run.add_argument("--key", help="API key for Mode B")
    p_run.add_argument("--api", help="WS base URL")
    p_run.add_argument(
        "--param",
        "--params",
        dest="params",
        action="append",
        help="k=v procedure params (repeatable)",
    )
    p_run.add_argument(
        "--skip-perm-check",
        action="store_true",
        help="skip macOS perms check before running locally",
    )
    p_run.add_argument(
        "--verify-mode",
        choices=("soft", "hard"),
        default=None,
        help="override verify_mode for JSON procedures (default soft)",
    )
    p_run.set_defaults(func=cmd_run)

    p_proc = sub.add_parser("procedures", help="list discoverable local procedures")
    p_proc.add_argument("--json", action="store_true", help="emit JSON catalog")
    p_proc.set_defaults(func=cmd_procedures)

    p_init = sub.add_parser("init", help="write ~/.case-sdk/config.toml")
    p_init.add_argument("--key", help="API key to store")
    p_init.add_argument("--api", help="WS base URL to store")
    p_init.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )
    p_init.set_defaults(func=cmd_init)

    p_doc = sub.add_parser("doctor", help="check perms, config, server")
    p_doc.set_defaults(func=cmd_doctor)

    p_ax = sub.add_parser("ax-tree", help="dump AX tree or find elements")
    p_ax.add_argument(
        "app",
        nargs="?",
        default=None,
        help="app name substring (default: frontmost if --frontmost)",
    )
    p_ax.add_argument(
        "--frontmost",
        action="store_true",
        help="use frontmost app (default when app name omitted)",
    )
    p_ax.add_argument("--depth", type=int, default=12, help="max tree depth")
    p_ax.add_argument(
        "--max-children",
        type=int,
        default=80,
        help="max children per node",
    )
    p_ax.add_argument(
        "--max-nodes", type=int, default=2500, help="global node cap"
    )
    p_ax.add_argument(
        "--roles",
        help="comma-separated AXRole filter (e.g. AXButton,AXTextField)",
    )
    p_ax.add_argument(
        "--find",
        action="store_true",
        help="search mode: emit element refs instead of full tree",
    )
    p_ax.add_argument("--role", help="find: match AXRole exactly")
    p_ax.add_argument("--title", help="find: match AXTitle exactly")
    p_ax.add_argument(
        "--title-contains", help="find: case-insensitive substring match"
    )
    p_ax.add_argument(
        "--limit", type=int, default=20, help="find: max results"
    )
    p_ax.add_argument(
        "--skip-perm-check",
        action="store_true",
        help="skip macOS perms check",
    )
    p_ax.set_defaults(func=cmd_ax_tree)

    p_vl = sub.add_parser("verify-list", help="list registered verify step names")
    p_vl.set_defaults(func=cmd_verify_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
