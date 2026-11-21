"""Hashes of the files an apply wrote and has not yet committed or rolled back.

A killed run leaves its manifest and lockfile writes in the worktree. Only a path
listed here whose bytes still match is that run's own leftover; anything else
dirty is the operator's. The file sits in the git dir, so it never shows as
untracked and is removed with the clone.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from pathlib import Path

from repo_ops_core.git import run_git

_LOCK = threading.Lock()
_NAME = "repo-ops-inflight.json"


def _file(repo: Path) -> Path | None:
    proc = run_git(repo, "rev-parse", "--absolute-git-dir", timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()) / _NAME


def _read(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _write(path: Path, entries: dict[str, str]) -> None:
    if not entries:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(entries, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load(repo: Path) -> dict[str, str]:
    """Repo-relative path to the content hash apply left it with."""
    path = _file(repo)
    return _read(path) if path else {}


def record(repo: Path, entries: dict[str, str]) -> None:
    path = _file(repo)
    if path is None or not entries:
        return
    with _LOCK:
        _write(path, {**_read(path), **entries})


def forget(repo: Path, rels: Iterable[str]) -> None:
    path = _file(repo)
    if path is None:
        return
    drop = set(rels)
    with _LOCK:
        _write(path, {k: v for k, v in _read(path).items() if k not in drop})
