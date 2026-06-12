"""uv upgrade check/apply."""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import resolve_timeout_apply, resolve_timeout_check


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


def path_is_uv_workspace_child(mdir: Path, root: Path, members: set[str]) -> bool:
    """True when *mdir* is a member of the uv workspace rooted at *root*."""
    try:
        rel = mdir.relative_to(root).as_posix()
    except ValueError:
        return False
    for pattern in members:
        pat = pattern.replace("*", "")
        if rel == pattern.rstrip("/*") or _under(rel, pat):
            return True
        if pattern.endswith("/*") and _under(rel, pattern[:-2]):
            return True
    return False


def _under(rel: str, prefix: str) -> bool:
    """True when *rel* equals *prefix* or sits under it on a path boundary.

    Separator-aware so ``packages/foo`` does not match the sibling
    ``packages/foobar`` (#59).
    """
    prefix = prefix.rstrip("/")
    return rel == prefix or rel.startswith(prefix + "/")


def check_uv(ws: Workspace) -> list[Change]:
    proc = run(
        ["uv", "lock", "--upgrade", "--dry-run"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if "would upgrade" in line.lower() or "->" in line:
            m = re.search(r"(\S+)\s+([\d.]+)\s*->\s*([\d.]+)", line)
            if m:
                changes.append(Change(m.group(1), m.group(2), m.group(3)))
    if not changes and "would change" in (proc.stdout + proc.stderr).lower():
        changes.append(Change("(lockfile)", "current", "upgraded"))
    return changes


def apply_uv(ws: Workspace) -> None:
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=resolve_timeout_apply())
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")
