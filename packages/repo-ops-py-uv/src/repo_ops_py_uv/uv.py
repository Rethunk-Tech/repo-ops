"""uv upgrade check/apply."""

from __future__ import annotations

import re

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY


def check_uv(ws: Workspace) -> list[Change]:
    proc = run(["uv", "lock", "--upgrade", "--dry-run"], ws.path)
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
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")
