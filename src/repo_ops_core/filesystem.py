"""Filesystem helpers shared by repo-ops commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repo_ops_core.constants import EXCLUDE_PATH_FRAGMENTS


def contained_path(root: Path, rel: str) -> Path | None:
    """Resolve *rel* under *root* when it cannot escape *root*.

    Returns None for empty, NUL, absolute, or ``..``-bearing relative paths,
    and for any path whose resolved location is not inside *root*.
    """
    if not rel or "\0" in rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or os.pardir in candidate.parts:
        return None
    root_res = root.resolve()
    full = (root_res / candidate).resolve()
    try:
        full.relative_to(root_res)
    except ValueError:
        return None
    return full


def is_under_root(root: Path, path: Path) -> bool:
    """True when *path* resolves inside *root* (symlinks followed)."""
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def write_json_atomic(path: Path, payload: Any) -> Path:
    """Serialise ``payload`` to ``path`` via temp file + atomic rename; returns the path.

    Only the parent is resolved, so a symlink at *path* is replaced rather than
    followed.
    """
    path = Path(path).expanduser()
    parent = path.parent.resolve()
    dest = parent / path.name
    parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{dest.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp_path, dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return dest.resolve()


# ponytail: keep-last-N pruning; age-based expiry if runs ever need keeping longer.
_RUNS_KEPT = 50


def new_run_dir(command: str) -> Path:
    """Create ``$XDG_STATE_HOME/repo-ops/runs/<utc>-<command>/`` and prune old runs.

    Every plan/apply writes its transcript and JSON report here, so no run needs
    a flag to be inspectable afterwards. Only the newest ``_RUNS_KEPT`` survive.
    """
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    runs = state / "repo-ops" / "runs"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = runs / f"{stamp}-{command}"
    run_dir.mkdir(parents=True)
    for old in sorted(p for p in runs.iterdir() if p.is_dir())[:-_RUNS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)
    return run_dir


def is_workspace_member(mdir: Path, root: Path, patterns: set[str]) -> bool:
    """True when *mdir* is a workspace member of *root* under one of *patterns*.

    Patterns are the manager's member globs (npm/yarn ``workspaces``, uv and
    Cargo ``members``). Matching is separator-aware, so ``packages/foo`` never
    matches the sibling ``packages/foobar``.
    """
    try:
        rel = mdir.relative_to(root).as_posix()
    except ValueError:
        return False
    for pattern in patterns:
        if "*" in pattern:
            if _workspace_glob_match(rel, pattern):
                return True
        elif _under(rel, pattern):
            return True
    return False


def _workspace_glob_match(rel: str, pattern: str) -> bool:
    """npm/yarn workspace glob: ``*`` is one path segment, ``**`` is any depth."""
    i = 0
    parts: list[str] = []
    while i < len(pattern):
        if pattern.startswith("**", i):
            rest_idx = i + 2
            if rest_idx < len(pattern) and pattern[rest_idx] == "/":
                parts.append("(?:.*/)?")
                i = rest_idx + 1
            else:
                parts.append(".*")
                i = rest_idx
            continue
        if pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
            continue
        j = i + 1
        while j < len(pattern) and pattern[j] != "*":
            j += 1
        parts.append(re.escape(pattern[i:j]))
        i = j
    return re.fullmatch("".join(parts), rel) is not None


def _under(rel: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return rel == prefix or rel.startswith(prefix + "/")


def should_exclude(path: str) -> bool:
    return any(fragment in path for fragment in EXCLUDE_PATH_FRAGMENTS)


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_toml_file(path: Path) -> dict[str, Any] | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def snapshot_file(path: Path, root: Path) -> bytes | None:
    """Capture *path* for a later :func:`restore_file`, or ``None`` if absent.

    A symlink resolving inside *root* is captured through to its target, so the
    rollback writes the bytes back and leaves the link in place. One resolving
    outside *root* yields ``None``: writing through it would touch a file the
    run does not own.
    """
    if path.is_symlink() and not is_under_root(root, path):
        return None
    if not path.is_file():
        return None
    return path.read_bytes()


def restore_file(path: Path, snapshot: bytes | None, root: Path) -> None:
    """Undo a targeted-bump/clamp attempt: restore *path* to its pre-attempt state."""
    if path.is_symlink():
        if is_under_root(root, path):
            path = path.resolve()
        elif snapshot is None:
            # An escaping link is never snapshotted, so it is not ours to remove.
            return
        else:
            path.unlink()
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)
