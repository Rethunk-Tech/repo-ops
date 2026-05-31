"""npm upgrade check/apply."""

from __future__ import annotations

import json

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY


def check_npm(ws: Workspace) -> list[Change]:
    proc = run(["npm", "outdated", "--json"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "npm outdated failed")
    if not proc.stdout.strip():
        return []
    data = json.loads(proc.stdout)
    changes: list[Change] = []
    for name, meta in data.items():
        if isinstance(meta, dict):
            changes.append(
                Change(
                    name,
                    str(meta.get("current", "?")),
                    str(meta.get("latest", "?")),
                )
            )
    return changes


def apply_npm(ws: Workspace) -> None:
    proc = run(["npm", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "npm update failed")
    proc2 = run(["npm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "npm install failed")
