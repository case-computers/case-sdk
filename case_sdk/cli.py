"""case-sdk command-line interface.

Subcommands:
  - ``run <path-or-name>``  execute a procedure (Mode A if path, Mode B if name)
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

from case_sdk import Runtime, __version__, check_perms, ensure_perms
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

    target_path = Path(args.target)
    is_local = target_path.exists() and target_path.is_file()

    if is_local:
        if not args.skip_perm_check:
            ensure_perms()
        rt = Runtime(api_key=api_key, api_base=api_base)
        try:
            result = rt.execute_file(args.target, **params)
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    else:
        if not api_key:
            print(
                "error: remote procedure requires --key, "
                "CASE_SDK_API_KEY env var, or api_key in ~/.case-sdk/config.toml",
                file=sys.stderr,
            )
            return 2
        rt = Runtime(api_key=api_key, api_base=api_base)
        try:
            result = rt.execute(args.target, **params)
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    print(json.dumps(result, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
    p_run.add_argument("target", help="local file path or named procedure")
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
    p_run.set_defaults(func=cmd_run)

    p_init = sub.add_parser("init", help="write ~/.case-sdk/config.toml")
    p_init.add_argument("--key", help="API key to store")
    p_init.add_argument("--api", help="WS base URL to store")
    p_init.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )
    p_init.set_defaults(func=cmd_init)

    p_doc = sub.add_parser("doctor", help="check perms, config, server")
    p_doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
