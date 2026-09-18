"""Filesystem helpers shared by repo-ops commands."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repo_ops_core.constants import EXCLUDE_PATH_FRAGMENTS


def write_json_atomic(path: Path, payload: Any) -> Path:
    """Serialise ``payload`` to ``path`` via temp file + atomic rename; returns the path."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


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


def snapshot_file(path: Path) -> bytes | None:
    """Capture a file's bytes for a later :func:`restore_file`, or ``None`` if absent."""
    return path.read_bytes() if path.is_file() else None


def restore_file(path: Path, snapshot: bytes | None) -> None:
    """Undo a targeted-bump/clamp attempt: restore *path* to its pre-attempt state."""
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)
