"""Filesystem helpers shared by repo-ops commands."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from repo_ops_core.constants import EXCLUDE_PATH_FRAGMENTS

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def write_json_atomic(path: Path, payload: Any, *, compact: bool = False) -> Path:
    """Serialise ``payload`` to ``path`` via temp file + atomic rename.

    Generic counterpart to the bundle-specific ``write_json_report`` in
    repo-ops-upgrade: any command can write its JSON document atomically.
    Returns the resolved path. ``compact`` emits single-line JSON.
    """
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=None if compact else 2) + "\n"
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
