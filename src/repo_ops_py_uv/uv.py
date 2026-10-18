"""uv workspace member discovery."""

from __future__ import annotations

from pathlib import Path

from repo_ops_core.filesystem import read_toml_file


def uv_workspace_members(mdir: Path) -> set[str]:
    """Return the ``[tool.uv.workspace].members`` globs declared in *mdir*.

    Empty when *mdir* is not a uv workspace root. Members are governed by the
    root ``uv.lock`` and must not be enumerated as standalone workspaces.
    """
    d = read_toml_file(mdir / "pyproject.toml") or {}
    ws = ((d.get("tool") or {}).get("uv") or {}).get("workspace") or {}
    members = ws.get("members")
    if not isinstance(members, list):
        return set()
    return {m.rstrip("/") for m in members if isinstance(m, str)}
