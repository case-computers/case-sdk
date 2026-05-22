"""Local procedure discovery — resolve names to .py or .json files.

Search order (first match wins): ``CASE_SDK_PROCEDURES_PATH``,
``~/.case/procedures``, ``~/.case-sdk/procedures``, packaged
``case_sdk/builtin_procedures/``. Procedure names use forward slashes for
nesting (e.g. ``davinci/append_to_timeline``). Symlinks whose target
escapes the search root are skipped with a warning.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

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
    """Return ``(qualified_name, path)``; first search root wins on name clash.

    - Files whose resolved path escapes the search root (symlink hop) are
      skipped with a ``RuntimeWarning``.
    - When a built-in name is shadowed by a user-dir file, a single
      ``RuntimeWarning`` is emitted (the user-dir file still wins).
    """
    by_name: dict[str, Path] = {}
    name_origin: dict[str, Path] = {}
    try:
        builtin_root: Path | None = _BUILTIN.resolve()
    except (OSError, RuntimeError):
        builtin_root = None

    for root in search_paths():
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _PROC_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                continue
            try:
                resolved.relative_to(root_resolved)
            except ValueError:
                warnings.warn(
                    f"case-sdk: skipping {path} (resolves outside search "
                    f"root {root})",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            rel = path.relative_to(root).with_suffix("")
            key = rel.as_posix()
            if key in by_name:
                if (
                    builtin_root is not None
                    and root_resolved == builtin_root
                    and name_origin.get(key) != builtin_root
                ):
                    warnings.warn(
                        f"case-sdk: built-in procedure '{key}' is shadowed "
                        f"by {by_name[key]}",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                continue
            by_name[key] = resolved
            name_origin[key] = root_resolved
    return sorted(by_name.items())


def read_procedure_metadata(path: Path) -> dict[str, Any]:
    """Read top-level metadata from a procedure file without executing it.

    Returns a dict with optional ``description`` / ``verify_mode`` /
    ``user_action_prompt`` plus a guaranteed ``requires_user_action`` bool.
    Non-JSON files (``.py`` generators) get ``{"requires_user_action": False}``
    — Python procedures handle their own user-interaction logic.

    Malformed JSON is swallowed (returns the default) so a single broken
    file under ``~/.case/procedures`` does not poison the catalog.
    """
    meta: dict[str, Any] = {"requires_user_action": False}
    if path.suffix.lower() != ".json":
        return meta
    try:
        import json

        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return meta
    if not isinstance(doc, dict):
        return meta
    if isinstance(doc.get("description"), str):
        meta["description"] = doc["description"]
    if doc.get("verify_mode") in ("soft", "hard"):
        meta["verify_mode"] = doc["verify_mode"]
    if bool(doc.get("requires_user_action")):
        meta["requires_user_action"] = True
        if isinstance(doc.get("user_action_prompt"), str):
            meta["user_action_prompt"] = doc["user_action_prompt"]
    return meta


def list_procedures() -> list[dict[str, Any]]:
    """Return catalog entries.

    Each entry has ``name``, ``path``, ``kind`` and may include
    ``description``, ``requires_user_action`` (when true), and
    ``user_action_prompt`` (when ``requires_user_action`` is true).
    """
    entries: list[dict[str, Any]] = []
    for name, path in _collect_files():
        item: dict[str, Any] = {
            "name": name,
            "path": str(path),
            "kind": path.suffix.lower().lstrip("."),
        }
        meta = read_procedure_metadata(path)
        if "description" in meta:
            item["description"] = meta["description"]
        if meta.get("requires_user_action"):
            item["requires_user_action"] = True
            if "user_action_prompt" in meta:
                item["user_action_prompt"] = meta["user_action_prompt"]
        entries.append(item)
    return sorted(entries, key=lambda e: e["name"])


def resolve_procedure(name: str, allow_arbitrary: bool = False) -> Path:
    """Resolve a procedure name or filesystem path.

    When ``allow_arbitrary`` is False (default), a direct filesystem path is
    only honoured if it resolves inside one of the configured search roots.
    Outside-root paths raise :class:`ProcedureNotFound` so a malicious caller
    can't aim ``execute_file`` at arbitrary ``.py`` files on disk.
    """
    candidate = Path(name).expanduser()
    if candidate.is_file():
        resolved = candidate.resolve()
        if allow_arbitrary:
            return resolved
        for root in search_paths():
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return resolved
        raise ProcedureNotFound(
            f"refusing to run procedure file outside search roots "
            f"(use --allow-arbitrary-py to override): {candidate}"
        )

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
