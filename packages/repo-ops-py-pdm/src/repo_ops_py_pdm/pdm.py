"""PDM upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_pdm(ws: Workspace) -> list[Change]:
    """Return pending upgrades for a PDM-managed workspace.

    Runs ``pdm update --dry-run`` and parses lines of the form::

        - name 1.2.3 -> 1.3.0

    Falls back to an empty list with a WARN when ``pdm`` is absent so the
    workspace degrades gracefully (report-only, no error).
    """
    if not shutil.which("pdm"):
        print(f"WARN {ws.path}: pdm not on PATH", file=sys.stderr)
        return []
    proc = run(
        ["pdm", "update", "--dry-run"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pdm update --dry-run failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        # PDM dry-run lines look like:
        #   - requests 2.28.0 -> 2.31.0
        #   ~ requests 2.28.0 -> 2.31.0
        m = re.search(r"[-~]\s+(\S+)\s+([\d][^\s]*)\s+->\s+([\d][^\s]*)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes


def apply_pdm(ws: Workspace) -> None:
    """Run ``pdm update`` to upgrade all dependencies in the lockfile."""
    if not shutil.which("pdm"):
        raise RuntimeError(f"pdm not on PATH for {ws.path}")
    proc = run(["pdm", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pdm update failed")
