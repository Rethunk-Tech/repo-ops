"""Directory walks shared by inventory counting and upgrade discovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import should_exclude

PY_MANIFEST_NAMES = frozenset(
    {"pyproject.toml", "requirements.in", "requirements.txt", "Pipfile"}
)


def iter_scanned_dirs(root: Path) -> Iterator[tuple[Path, set[str]]]:
    """Walk *root*, pruning SKIP_DIRS and excluded paths.

    Yields ``(directory, filenames)`` for each visited directory.
    """
    if not root.is_dir() or should_exclude(str(root)):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        mdir = Path(dirpath)
        if should_exclude(str(mdir)):
            dirnames.clear()
            continue
        yield mdir, set(filenames)


def manifest_kinds(names: set[str]) -> list[str]:
    """Return ecosystem tags signaled by *names* (node, go, rust, py)."""
    kinds: list[str] = []
    if "package.json" in names:
        kinds.append("node")
    if "go.mod" in names:
        kinds.append("go")
    if "Cargo.toml" in names:
        kinds.append("rust")
    if names & PY_MANIFEST_NAMES:
        kinds.append("py")
    return kinds
