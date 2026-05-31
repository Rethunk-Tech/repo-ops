"""bun upgrade check/apply."""

from __future__ import annotations

import json

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY


def check_bun(ws: Workspace) -> list[Change]:
    proc = run(["bun", "outdated"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed"
        )
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] not in ("Package", "bun", "─"):
            changes.append(Change(parts[0], parts[1], parts[-1]))
    if not changes:
        proc2 = run(["bun", "outdated", "--json"], ws.path)
        if proc2.returncode in (0, 1) and proc2.stdout.strip():
            try:
                data = json.loads(proc2.stdout)
                for name, meta in (data if isinstance(data, dict) else {}).items():
                    if isinstance(meta, dict):
                        cur = meta.get("current") or meta.get("installed", "?")
                        lat = meta.get("latest", "?")
                        if cur != lat:
                            changes.append(Change(name, str(cur), str(lat)))
            except json.JSONDecodeError:
                pass
    return changes


def apply_bun(ws: Workspace) -> None:
    proc = run(["bun", "update", "--latest"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")
