"""npm upgrade check/apply."""

from __future__ import annotations

import json

from repo_ops_core.process import run
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY, TIMEOUT_CHECK


def check_npm(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated npm packages.

    Returns ``(changes, behind_latest)`` where ``changes`` contains packages
    with in-range upgrades (``wanted`` > ``current``) and ``behind_latest``
    holds packages whose ``latest`` exceeds ``current`` but the in-range
    ``wanted`` version equals ``current`` (i.e. only a range-breaking bump
    is available).
    """
    proc = run(["npm", "outdated", "--json"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "npm outdated failed")
    if not proc.stdout.strip():
        return [], []
    data = json.loads(proc.stdout)
    changes: list[Change] = []
    behind_latest: list[Change] = []
    for name, meta in data.items():
        if not isinstance(meta, dict):
            continue
        cur = str(meta.get("current", "?"))
        wanted = str(meta.get("wanted", "?"))
        latest = str(meta.get("latest", "?"))
        if cur != wanted and wanted not in ("?", ""):
            # In-range upgrade available.
            changes.append(Change(name, cur, wanted))
        elif latest not in ("?", "") and cur != latest:
            # latest exceeds current but wanted == current (out-of-range).
            behind_latest.append(Change(name, cur, latest))
    return changes, behind_latest


def apply_npm(ws: Workspace) -> None:
    changes, _behind = check_npm(ws)
    proc = run(["npm", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "npm update failed")
    proc2 = run(["npm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "npm install failed")
    if changes:
        assert_targets_resolved(ws, changes, manager="npm")
