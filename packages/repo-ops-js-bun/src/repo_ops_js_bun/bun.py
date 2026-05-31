"""bun upgrade check/apply."""

from __future__ import annotations

import json
import re

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY

_INVALID_BUN_NAME = re.compile(r"^[|?\s]+$|Package|^─")


def _valid_bun_package_name(name: str) -> bool:
    n = (name or "").strip()
    if not n or _INVALID_BUN_NAME.search(n):
        return False
    if "|" in n or n in ("Package", "bun", "─"):
        return False
    return True


def _changes_from_bun_json(stdout: str) -> list[Change]:
    changes: list[Change] = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return changes
    if not isinstance(data, dict):
        return changes
    for name, meta in data.items():
        if not _valid_bun_package_name(str(name)) or not isinstance(meta, dict):
            continue
        cur = meta.get("current") or meta.get("installed", "?")
        lat = meta.get("latest", "?")
        if cur != lat:
            changes.append(Change(str(name), str(cur), str(lat)))
    return changes


def _changes_from_bun_text(stdout: str) -> list[Change]:
    changes: list[Change] = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        if not _valid_bun_package_name(name):
            continue
        to_ver = parts[-1]
        if to_ver in ("|", "→", "?"):
            continue
        from_ver = parts[1] if len(parts) >= 3 else "?"
        changes.append(Change(name, from_ver, to_ver))
    return changes


def check_bun(ws: Workspace) -> list[Change]:
    proc_json = run(["bun", "outdated", "--json"], ws.path)
    if proc_json.returncode in (0, 1) and proc_json.stdout.strip():
        try:
            data = json.loads(proc_json.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return _changes_from_bun_json(proc_json.stdout)

    proc = run(["bun", "outdated"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed"
        )
    return _changes_from_bun_text(proc.stdout)


def apply_bun(ws: Workspace) -> None:
    """Apply upgrades reported by ``check_bun`` without rewriting ranges to ``latest``."""
    changes = check_bun(ws)
    if not changes:
        return
    targets: list[str] = []
    for ch in changes:
        name = (ch.name or "").strip()
        version = (ch.to or "").strip()
        if not name or not version or version in ("?", "latest", "|"):
            continue
        if not _valid_bun_package_name(name):
            continue
        targets.append(f"{name}@{version}")
    if not targets:
        proc = run(["bun", "update"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "bun update failed")
        return
    proc = run(["bun", "update", *targets], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")
