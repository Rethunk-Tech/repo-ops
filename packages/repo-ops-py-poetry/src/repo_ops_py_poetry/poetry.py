"""poetry upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_poetry(ws: Workspace) -> list[Change]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return []
    proc = run(
        ["poetry", "update", "--dry-run"], ws.path, timeout=resolve_timeout_check()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        m = re.search(
            r"(?:Updating|Upgrading)\s+(\S+)\s+\(([^)]+)\s+->\s+([^)]+)\)", line
        )
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes


def apply_poetry(ws: Workspace) -> None:
    if not shutil.which("poetry"):
        raise RuntimeError(f"poetry not on PATH for {ws.path}")
    proc = run(["poetry", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")
