"""Keep a repo's SOUP inventory table in step with a dependency bump.

The new version is the one ``bun.lock`` resolved after the install, not the manifest range, falling back to the
manifest's own version when the package is not in the lock.

Opt-in with ``[soup] path = "docs/SOUP.md"`` in the repo's ``.repo-ops.toml``. Rows look like
``| `name` | `1.2.3` | ... |``; only the Version cell of a row whose package was bumped is rewritten.
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.config import load_repo_config
from repo_ops_models import Change

_ROW = re.compile(r"^(\|\s*`(?P<name>[^`]+)`\s*\|\s*`)(?P<version>[^`]+)(`\s*\|)")
_RANGE_PREFIX = re.compile(r"^[\^~>=<v\s]+")
_VERSION = re.compile(r"^\d+(\.\d+)*([-+][0-9A-Za-z.+-]+)?$")


def _bumped_versions(changes: list[Change]) -> dict[str, str]:
    """Package name to its new concrete version; specs that are not versions (tags, urls) drop out."""
    out: dict[str, str] = {}
    for change in changes:
        version = _RANGE_PREFIX.sub("", change.to)
        if _VERSION.match(version):
            out[change.name] = version
    return out


_LOCK_ENTRY = re.compile(r'^\s*"(?P<key>[^"]+)":\s*\["(?P<spec>[^"]+)"', re.MULTILINE)


def _resolved_versions(lock_dirs: list[Path]) -> dict[str, str]:
    """Package name to the version the first ``bun.lock`` in lock_dirs resolved for it."""
    for directory in lock_dirs:
        lock = directory / "bun.lock"
        if not lock.is_file():
            continue
        out: dict[str, str] = {}
        for m in _LOCK_ENTRY.finditer(lock.read_text(encoding="utf-8")):
            name, _, version = m["spec"].rpartition("@")
            if name == m["key"]:  # a top-level entry, not a nested copy under another package
                out[name] = version
        return out
    return {}


def sync_soup_versions(
    repo_root: Path, changes: list[Change], lock_dirs: list[Path] | None = None
) -> bool:
    """Rewrite Version cells in the configured SOUP table; True when the file changed."""
    table = load_repo_config(repo_root).get("soup")
    rel = table.get("path") if isinstance(table, dict) else None
    if not isinstance(rel, str) or not rel:
        return False
    path = repo_root / rel
    bumped = _bumped_versions(changes)
    bumped |= {n: v for n, v in _resolved_versions(lock_dirs or [repo_root]).items() if n in bumped}
    if not bumped or not path.is_file():
        return False

    def rewrite(line: str) -> str:
        m = _ROW.match(line)
        new = bumped.get(m["name"]) if m else None
        if m is None or new is None or m["version"] == new:
            return line
        return f"{m.group(1)}{new}{m.group(4)}{line[m.end() :]}"

    text = path.read_text(encoding="utf-8")
    updated = "".join(rewrite(line) for line in text.splitlines(keepends=True))
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True
