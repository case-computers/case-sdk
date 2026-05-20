"""Accessibility tree introspection for procedure authoring.

Exposes a stable JSON representation of an app's AX hierarchy so users and
agents can discover ``element_ref`` values for ``ax_press`` / ``ax_set_value``.

Requires macOS + Accessibility permission (same as the harness).
"""

from __future__ import annotations

from typing import Any

# Defaults — keep trees bounded on large apps (DaVinci, browsers).
DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_CHILDREN = 80
DEFAULT_MAX_NODES = 2500


class InspectError(Exception):
    """Raised when AX introspection cannot proceed (no app, no perms, etc.)."""


def app_tree(
    app_name: str | None = None,
    *,
    frontmost: bool = False,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
    max_nodes: int = DEFAULT_MAX_NODES,
    roles: list[str] | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable AX tree for one application.

    Args:
        app_name: localized app name substring match (e.g. ``"DaVinci Resolve"``).
            Ignored when ``frontmost=True``.
        frontmost: if True, use the frontmost app instead of ``app_name``.
        max_depth: stop descending after this many levels (root = 0).
        max_children: cap children listed per node (extras omitted with a count).
        max_nodes: global cap; traversal stops when reached.
        roles: if set, only include nodes whose AXRole is in this list (still
            descends through non-matching parents to reach deeper matches).

    Returns:
        dict with ``app``, ``pid``, and nested ``tree`` node, or raises
        :class:`InspectError`.
    """
    root_elem, meta = _get_app_element(app_name=app_name, frontmost=frontmost)
    counter = _NodeCounter(max_nodes=max_nodes)
    role_set = set(roles) if roles else None
    tree = _serialize_node(
        root_elem,
        depth=0,
        max_depth=max_depth,
        max_children=max_children,
        counter=counter,
        role_set=role_set,
        path=[],
    )
    return {
        **meta,
        "truncated": counter.truncated,
        "nodes": counter.count,
        "tree": tree,
    }


def find_elements(
    app_name: str | None = None,
    *,
    frontmost: bool = False,
    role: str | None = None,
    title: str | None = None,
    title_contains: str | None = None,
    max_results: int = 20,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[dict[str, Any]]:
    """Search the AX tree and return matching ``element_ref`` dicts.

    Each result includes ``ref`` (usable with ``ax_press`` / ``ax_set_value``),
    plus human-readable fields (role, title, path).
    """
    root_elem, meta = _get_app_element(app_name=app_name, frontmost=frontmost)
    matches: list[dict[str, Any]] = []
    _find_recursive(
        root_elem,
        meta_app=meta["app"],
        depth=0,
        max_depth=max_depth,
        path=[],
        role=role,
        title=title,
        title_contains=title_contains,
        matches=matches,
        max_results=max_results,
    )
    return matches


def resolve_element_ref(element_ref: dict[str, Any] | str | None) -> Any:
    """Resolve an ``element_ref`` dict to an atomacos element, or ``None``.

    Supported ref shapes::

        {"role": "AXButton", "title": "Save"}
        {"app": "DaVinci Resolve", "path": [{"role": "AXWindow"}, {"role": "AXButton", "title": "Save"}]}
        {"path": [0, 2, 1]}   # child indices from app root (with optional "app")

    When ``app`` is omitted, uses the frontmost application.
    """
    if not isinstance(element_ref, dict):
        return None
    try:
        import atomacos  # noqa: WPS433
    except Exception:
        return None

    app_name = element_ref.get("app")
    path = element_ref.get("path")
    role = element_ref.get("role")
    title = element_ref.get("title")
    title_contains = element_ref.get("title_contains")
    value = element_ref.get("value")

    try:
        root, _ = _get_app_element(
            app_name=str(app_name) if app_name else None,
            frontmost=not app_name,
        )
    except InspectError:
        return None

    if path is not None:
        return _resolve_by_path(root, path)

    if not (role or title or value):
        return None

    if title_contains is not None:
        return _walk_for_title_contains(
            root, role=role, title_contains=str(title_contains), depth=0
        )
    return _walk_for_match(root, role=role, title=title, value=value, depth=0)


# ---- internals -----------------------------------------------------------


class _NodeCounter:
    def __init__(self, max_nodes: int) -> None:
        self.max_nodes = max_nodes
        self.count = 0
        self.truncated = False

    def tick(self) -> bool:
        """Increment; return False if global cap exceeded."""
        self.count += 1
        if self.count > self.max_nodes:
            self.truncated = True
            return False
        return True


def _get_app_element(
    *,
    app_name: str | None = None,
    frontmost: bool = False,
) -> tuple[Any, dict[str, Any]]:
    try:
        import atomacos  # noqa: WPS433
    except Exception as exc:
        raise InspectError(
            "atomacos is required for AX introspection (macOS only)"
        ) from exc

    if frontmost or not app_name:
        try:
            app = atomacos.getFrontmostApp()
            return app, _app_meta(app, fallback_name=app_name or "")
        except Exception as exc:
            raise InspectError(f"could not get frontmost app: {exc}") from exc

    pid = _find_pid_by_name(app_name or "")
    if pid is None:
        raise InspectError(f"no running app matching {app_name!r}")
    try:
        app = atomacos.getAppRefByPid(pid)
    except Exception as exc:
        raise InspectError(f"could not bind AX to pid {pid}: {exc}") from exc
    return app, _app_meta(app, fallback_name=app_name or "")


# Background / overlay services whose localizedName can sneak ahead of the
# intended target during substring matching. Skipped on the fallback pass so
# something like `ax-tree TextEdit` doesn't resolve to QuickLookUIService.
_HELPER_LOCALIZED_NAMES = frozenset(
    {
        "QuickLookUIService",
        "CursorUIViewService",
        "WindowServer",
        "Antigravity",
        "Notification Center",
        "Spotlight",
        "UserNotificationCenter",
        "universalAccessAuthWarn",
    }
)


def _find_pid_by_name(name: str) -> int | None:
    """Resolve an app name (or bundle id) to a PID.

    Resolution order:
      1. exact ``localizedName`` (case-insensitive)
      2. exact ``bundleIdentifier`` (e.g. ``com.apple.TextEdit``)
      3. exact ``bundleIdentifier == com.apple.{name}`` shortcut for Apple apps
      4. substring match on ``localizedName``, excluding known helper /
         overlay services (longest match wins for disambiguation)

    Returns ``None`` if no app matches.
    """
    try:
        from AppKit import NSWorkspace  # noqa: WPS433
    except Exception:
        return None
    name_clean = (name or "").strip()
    if not name_clean:
        return None
    target_lower = name_clean.lower()
    apple_id = f"com.apple.{name_clean}"
    workspace = NSWorkspace.sharedWorkspace()
    running = list(workspace.runningApplications())

    # Pass 1: exact localizedName (case-insensitive)
    for app in running:
        try:
            loc = app.localizedName() or ""
        except Exception:
            continue
        if loc.lower() == target_lower:
            try:
                return int(app.processIdentifier())
            except Exception:
                continue

    # Pass 2 & 3: exact bundleIdentifier or com.apple.<name>
    for app in running:
        try:
            bid = app.bundleIdentifier() or ""
        except Exception:
            continue
        if bid == name_clean or bid == apple_id:
            try:
                return int(app.processIdentifier())
            except Exception:
                continue

    # Pass 4: substring fallback (excluding helper services).
    best_pid: int | None = None
    best_len = -1
    for app in running:
        try:
            loc = app.localizedName() or ""
        except Exception:
            continue
        if loc in _HELPER_LOCALIZED_NAMES:
            continue
        loc_l = loc.lower()
        if target_lower in loc_l or loc_l in target_lower:
            if len(loc) > best_len:
                try:
                    best_pid = int(app.processIdentifier())
                    best_len = len(loc)
                except Exception:
                    continue
    return best_pid


def _app_meta(app: Any, *, fallback_name: str) -> dict[str, Any]:
    title = _safe_attr(app, "AXTitle") or fallback_name
    pid = _safe_attr(app, "AXPID")
    return {
        "app": str(title or fallback_name),
        "pid": int(pid) if pid is not None else None,
    }


def _serialize_node(
    elem: Any,
    *,
    depth: int,
    max_depth: int,
    max_children: int,
    counter: _NodeCounter,
    role_set: set[str] | None,
    path: list[int],
) -> dict[str, Any] | None:
    if not counter.tick():
        return None

    role = _safe_attr(elem, "AXRole")
    title = _safe_attr(elem, "AXTitle")
    subrole = _safe_attr(elem, "AXSubrole")
    desc = _safe_attr(elem, "AXDescription")
    value = _safe_attr(elem, "AXValue")
    enabled = _safe_attr(elem, "AXEnabled")
    pos = _safe_attr(elem, "AXPosition")
    size = _safe_attr(elem, "AXSize")

    node: dict[str, Any] = {"role": role}
    if title is not None:
        node["title"] = _truncate_str(title)
    if subrole is not None:
        node["subrole"] = subrole
    if desc is not None:
        node["description"] = _truncate_str(desc)
    if value is not None and _include_value(value):
        node["value"] = _truncate_str(value)
    if enabled is not None:
        node["enabled"] = bool(enabled)
    if pos is not None:
        node["position"] = _point_dict(pos)
    if size is not None:
        node["size"] = _size_dict(size)
    if path:
        node["path"] = list(path)

    if depth >= max_depth:
        node["depth_limit"] = True
        return node

    if role_set is not None and role not in role_set:
        # Still descend — parent may wrap a matching child.
        pass

    children_raw = _safe_children(elem)
    if not children_raw:
        return node

    children_out: list[dict[str, Any]] = []
    omitted = 0
    for idx, child in enumerate(children_raw):
        if idx >= max_children:
            omitted = len(children_raw) - max_children
            break
        child_node = _serialize_node(
            child,
            depth=depth + 1,
            max_depth=max_depth,
            max_children=max_children,
            counter=counter,
            role_set=role_set,
            path=path + [idx],
        )
        if child_node is None:
            break
        if role_set is None or child_node.get("role") in role_set or child_node.get("children"):
            children_out.append(child_node)
        elif _subtree_has_role(child_node, role_set):
            children_out.append(child_node)

    if children_out:
        node["children"] = children_out
    if omitted:
        node["children_omitted"] = omitted
    return node


def _subtree_has_role(node: dict[str, Any], role_set: set[str]) -> bool:
    if node.get("role") in role_set:
        return True
    for ch in node.get("children") or []:
        if _subtree_has_role(ch, role_set):
            return True
    return False


def _find_recursive(
    elem: Any,
    *,
    meta_app: str,
    depth: int,
    max_depth: int,
    path: list[int],
    role: str | None,
    title: str | None,
    title_contains: str | None,
    matches: list[dict[str, Any]],
    max_results: int,
) -> None:
    if len(matches) >= max_results or depth > max_depth:
        return

    elem_role = _safe_attr(elem, "AXRole")
    elem_title = _safe_attr(elem, "AXTitle")
    if _match_criteria(
        elem_role, elem_title, role=role, title=title, title_contains=title_contains
    ):
        ref_path = []
        if path:
            # Reconstruct role/title path for stable refs (indices are fragile).
            ref_path = [{"index": i} for i in path]
        matches.append(
            {
                "ref": {
                    "app": meta_app,
                    "role": elem_role,
                    **({"title": elem_title} if elem_title is not None else {}),
                    **({"path": ref_path} if path else {}),
                },
                "role": elem_role,
                "title": elem_title,
                "path": list(path),
            }
        )

    for idx, child in enumerate(_safe_children(elem)):
        _find_recursive(
            child,
            meta_app=meta_app,
            depth=depth + 1,
            max_depth=max_depth,
            path=path + [idx],
            role=role,
            title=title,
            title_contains=title_contains,
            matches=matches,
            max_results=max_results,
        )
        if len(matches) >= max_results:
            return


def _match_criteria(
    elem_role: str | None,
    elem_title: str | None,
    *,
    role: str | None,
    title: str | None,
    title_contains: str | None,
) -> bool:
    if role and elem_role != role:
        return False
    if title is not None and elem_title != title:
        return False
    if title_contains is not None:
        if not elem_title or title_contains.lower() not in str(elem_title).lower():
            return False
    return bool(role or title is not None or title_contains is not None)


def _resolve_by_path(root: Any, path: list[Any]) -> Any | None:
    current = root
    for segment in path:
        children = _safe_children(current)
        if not children:
            return None
        if isinstance(segment, int):
            if segment < 0 or segment >= len(children):
                return None
            current = children[segment]
            continue
        if isinstance(segment, dict):
            if "index" in segment:
                idx = int(segment["index"])
                if idx < 0 or idx >= len(children):
                    return None
                current = children[idx]
                continue
            seg_role = segment.get("role")
            seg_title = segment.get("title")
            seg_value = segment.get("value")
            found = None
            for child in children:
                if _ax_matches(
                    _safe_attr(child, "AXRole"),
                    child,
                    role=seg_role,
                    title=seg_title,
                    value=seg_value,
                ):
                    found = child
                    break
            if found is None:
                return None
            current = found
            continue
        return None
    return current


def _walk_for_title_contains(
    elem: Any, *, role: str | None, title_contains: str, depth: int
) -> Any | None:
    if depth > 24:
        return None
    elem_role = _safe_attr(elem, "AXRole")
    needle = title_contains.lower()
    if (not role or elem_role == role) and needle:
        elem_title = _safe_attr(elem, "AXTitle")
        if elem_title and needle in str(elem_title).lower():
            return elem
    for child in _safe_children(elem):
        match = _walk_for_title_contains(
            child, role=role, title_contains=title_contains, depth=depth + 1
        )
        if match is not None:
            return match
    return None


def _walk_for_match(
    elem: Any, *, role: Any, title: Any, value: Any, depth: int
) -> Any | None:
    if depth > 24:
        return None
    elem_role = _safe_attr(elem, "AXRole")
    if _ax_matches(elem_role, elem, role=role, title=title, value=value):
        return elem
    for child in _safe_children(elem):
        match = _walk_for_match(
            child, role=role, title=title, value=value, depth=depth + 1
        )
        if match is not None:
            return match
    return None


def _ax_matches(
    elem_role: str | None,
    elem: Any,
    *,
    role: Any,
    title: Any,
    value: Any,
) -> bool:
    if role and elem_role != role:
        return False
    if title is not None:
        if _safe_attr(elem, "AXTitle") != title:
            return False
    if value is not None:
        if _safe_attr(elem, "AXValue") != value:
            return False
    return any(x is not None for x in (role, title, value))


def _safe_attr(elem: Any, name: str) -> Any:
    try:
        return getattr(elem, name, None)
    except Exception:
        return None


def _safe_children(elem: Any) -> list[Any]:
    try:
        raw = elem.AXChildren or []
        return list(raw)
    except Exception:
        return []


def _truncate_str(val: Any, limit: int = 200) -> str:
    s = str(val)
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _include_value(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, (bytes, bytearray)):
        return False
    if isinstance(val, (list, tuple, dict)) and len(str(val)) > 200:
        return False
    return True


def _point_dict(pos: Any) -> dict[str, int] | None:
    try:
        return {"x": int(pos.x), "y": int(pos.y)}
    except Exception:
        return None


def _size_dict(size: Any) -> dict[str, int] | None:
    try:
        return {"width": int(size.width), "height": int(size.height)}
    except Exception:
        return None
