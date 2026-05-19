"""Local procedure discovery — resolve names to .py or .json files.

Search order (first match wins):

1. Paths from ``CASE_SDK_PROCEDURES_PATH`` (colon-separated directories)
2. ``~/.case/procedures``
3. ``~/.case-sdk/procedures``
4. Packaged ``case_sdk/builtin_procedures/``

Procedure names use forward slashes for nesting, e.g.
``davinci/append_to_timeline`` → ``.../davinci/append_to_timeline.json``.
"""

from __future__ import annotations

import os
from pathlib import Path

_BUILTIN = Path(__file__).resolve().parent / "builtin_procedures"
_USER_CASE = Path.home() / ".case" / "procedures"
_USER_SDK = Path.home() / ".case-sdk" / "procedures"

_PROC_SUFFIXES = (".json", ".py")


class ProcedureNotFound(FileNotFoundError):
    pass


def extra_search_paths() -> list[Path]:
    raw = os.environ.get("CASE_SDK_PROCEDURES_PATH", "")
    return [Path(p).expanduser().resolve() for p in raw.split(":") if p.strip()]


def search_paths() -> list[Path]:
    """Directories scanned in precedence order (first wins on name clash)."""
    paths: list[Path] = []
    for p in extra_search_paths():
        if p.is_dir() and p not in paths:
            paths.append(p)
    for p in (_USER_CASE, _USER_SDK, _BUILTIN):
        if p.is_dir() and p not in paths:
            paths.append(p)
    return paths


def _collect_files() -> list[tuple[str, Path]]:
    """Return ``(qualified_name, path)``; first search root wins on name clash."""
    by_name: dict[str, Path] = {}
    for root in search_paths():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _PROC_SUFFIXES:
                continue
            rel = path.relative_to(root).with_suffix("")
            key = rel.as_posix()
            if key not in by_name:
                by_name[key] = path.resolve()
    return sorted(by_name.items())


def list_procedures() -> list[dict[str, str]]:
    """Return catalog entries ``{name, path, kind, description?}``."""
    entries: list[dict[str, str]] = []
    for name, path in _collect_files():
        item: dict[str, str] = {
            "name": name,
            "path": str(path),
            "kind": path.suffix.lower().lstrip("."),
        }
        if path.suffix.lower() == ".json":
            try:
                import json

                doc = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(doc.get("description"), str):
                    item["description"] = doc["description"]
            except Exception:
                pass
        entries.append(item)
    return sorted(entries, key=lambda e: e["name"])


def resolve_procedure(name: str) -> Path:
    """Resolve a procedure name or filesystem path."""
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    key = name.replace("\\", "/").strip("/")
    if key.endswith((".json", ".py")):
        key = Path(key).with_suffix("").as_posix()

    files = _collect_files()
    by_name = {n: p for n, p in files}

    if key in by_name:
        return by_name[key]

    for root in search_paths():
        for suffix in _PROC_SUFFIXES:
            direct = (root / key).with_suffix(suffix)
            if direct.is_file():
                return direct.resolve()

    stem_matches = [p for n, p in files if Path(n).name == key or p.stem == key]
    if len(stem_matches) == 1:
        return stem_matches[0]
    if len(stem_matches) > 1:
        names = [n for n, p in files if p in stem_matches]
        raise ProcedureNotFound(
            f"procedure name {name!r} is ambiguous; use a qualified name "
            f"such as {names[0]!r} or {names[1]!r}"
        )

    available = [e["name"] for e in list_procedures()]
    hint = ""
    if available:
        preview = ", ".join(available[:12])
        if len(available) > 12:
            preview += f", … (+{len(available) - 12} more)"
        hint = f" Known: {preview}."
    raise ProcedureNotFound(
        f"procedure {name!r} not found.{hint} "
        "Add JSON or Python under ~/.case/procedures (see `case-sdk init`)."
    )


def default_procedures_dir() -> Path:
    return _USER_CASE
