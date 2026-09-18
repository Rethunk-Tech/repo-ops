"""Directory walks shared by inventory counting and upgrade discovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import should_exclude


def iter_scanned_dirs(
    root: Path,
    *,
    prune: frozenset[str] | set[str] | None = None,
) -> Iterator[tuple[Path, set[str]]]:
    """Walk *root*, pruning SKIP_DIRS and excluded paths.

    *prune* holds realpath-normalised directories to skip entirely, along with
    everything beneath them. Callers use it to keep a nested checkout's files
    out of its parent's totals, so a repo and its submodules are not both
    credited with the same manifests.

    Yields ``(directory, filenames)`` for each visited directory.
    """
    if not root.is_dir() or should_exclude(str(root)):
        return
    pruned = prune or frozenset()
    if pruned and os.path.realpath(str(root)) in pruned:
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        mdir = Path(dirpath)
        if should_exclude(str(mdir)):
            dirnames.clear()
            continue
        if pruned:
            dirnames[:] = [
                d for d in dirnames if os.path.realpath(os.path.join(dirpath, d)) not in pruned
            ]
        yield mdir, set(filenames)
