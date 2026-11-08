"""Keep a repo's SOUP inventory table in step with a dependency bump.

The new version is the one the workspace lockfile resolved after the install, not
the manifest range. When the lockfile does not name a landed version, that row
is left alone.

Opt-in with ``[soup] path = "docs/SOUP.md"`` in the repo's ``.repo-ops.toml``. Rows look like
``| `name` | `1.2.3` | ... |``; only the Version cell of a row whose package was bumped is rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from repo_ops_core.config import load_repo_config
from repo_ops_core.filesystem import contained_path
from repo_ops_core.version_utils import semver_tuple
from repo_ops_lockfiles.parsers import (
    split_name_version,
    versions_bun_lock,
    versions_npm_lock,
    versions_pnpm_lock,
    versions_uv_lock,
    versions_yarn_lock,
)
from repo_ops_models import Change

_ROW = re.compile(r"^(\|\s*`(?P<name>[^`]+)`\s*\|\s*`)(?P<version>[^`]+)(`\s*\|)")
_RANGE_PREFIX = re.compile(r"^[\^~>=<v\s]+")
_VERSION = re.compile(r"^\d+(\.\d+)*([-+][0-9A-Za-z.+-]+)?$")


def _bumped_names(changes: list[Change]) -> set[str]:
    return {change.name for change in changes}


def _entries_to_versions(entries: set[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for entry in entries:
        name, ver = split_name_version(entry)
        if name and ver:
            out.setdefault(name, set()).add(ver)
    return out


def _highest(versions: set[str]) -> str:
    best = ""
    for ver in sorted(versions):
        try:
            if not best or semver_tuple(ver) > semver_tuple(best):
                best = ver
        except (TypeError, ValueError):
            continue
    return best


def _lockfile_versions(directory: Path) -> dict[str, set[str]]:
    if not directory.is_dir():
        return {}
    parsers: tuple[tuple[str, Callable[[Path], dict[str, set[str]]]], ...] = (
        ("bun.lock", lambda p: _entries_to_versions(versions_bun_lock(p))),
        ("package-lock.json", lambda p: _entries_to_versions(versions_npm_lock(p))),
        ("pnpm-lock.yaml", lambda p: _entries_to_versions(versions_pnpm_lock(p))),
        ("yarn.lock", lambda p: _entries_to_versions(versions_yarn_lock(p))),
        ("uv.lock", lambda p: _entries_to_versions(versions_uv_lock(p))),
    )
    for name, parse in parsers:
        path = directory / name
        if path.is_file():
            return parse(path)
    return {}


def _resolved_versions(lock_dirs: list[Path]) -> dict[str, set[str]]:
    """Package name to the versions the workspace lockfile resolved for it."""
    for directory in lock_dirs:
        found = _lockfile_versions(directory)
        if found:
            return found
    return {}


def sync_soup_versions(
    repo_root: Path, changes: list[Change], lock_dirs: list[Path] | None = None
) -> bool:
    """Rewrite Version cells in the configured SOUP table; True when the file changed."""
    table = load_repo_config(repo_root).get("soup")
    rel = table.get("path") if isinstance(table, dict) else None
    if not isinstance(rel, str) or not rel:
        return False
    path = contained_path(repo_root, rel)
    if path is None:
        return False
    names = _bumped_names(changes)
    if not names or not path.is_file():
        return False
    resolved = _resolved_versions(lock_dirs or [repo_root])
    landed = {
        name: version
        for name, versions in resolved.items()
        if _VERSION.match(_RANGE_PREFIX.sub("", version := _highest(versions)))
    }

    def rewrite(line: str) -> str:
        m = _ROW.match(line)
        new = landed.get(m["name"]) if m else None
        if m is None or new is None or m["version"] == new:
            return line
        # A dependency's bump can move a package the report never lists, like a peer
        # pinned exactly by the bumped one; its row is stale once the lockfile drops it.
        if m["name"] not in names and m["version"] in resolved[m["name"]]:
            return line
        return f"{m.group(1)}{new}{m.group(4)}{line[m.end() :]}"

    text = path.read_text(encoding="utf-8")
    updated = "".join(rewrite(line) for line in text.splitlines(keepends=True))
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True
