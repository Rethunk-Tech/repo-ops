"""poetry upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY


def check_poetry(ws: Workspace) -> list[Change]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return []
    proc = run(["poetry", "update", "--dry-run"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update --dry-run failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = re.search(r"Updating\s+(\S+)\s+\(([^)]+)\s+->\s+([^)]+)\)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes


def apply_poetry(ws: Workspace) -> None:
    proc = run(["poetry", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")
