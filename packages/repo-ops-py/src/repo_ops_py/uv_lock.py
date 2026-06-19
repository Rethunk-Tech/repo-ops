"""Shared uv.lock check/apply helpers (used by py-uv and py-hatch)."""

from __future__ import annotations

import re

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_uv_lock(ws: Workspace) -> list[Change]:
    """Run ``uv lock --upgrade --dry-run`` and return the detected changes."""
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


def apply_uv_lock(ws: Workspace) -> None:
    """Run ``uv lock --upgrade`` then ``uv sync``."""
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=resolve_timeout_apply())
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")
