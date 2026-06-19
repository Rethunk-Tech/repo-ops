"""npm upgrade check/apply."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_lockfiles.snapshots import (
    resolved_version_from_node_modules,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace

_NPM_TARGETED_BUMP_ENV = "REPO_OPS_NPM_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes")


def _npm_targeted_bump_enabled() -> bool:
    return os.environ.get(_NPM_TARGETED_BUMP_ENV, "").strip().lower() in _TRUE_VALUES


def _read_pkg_json(mdir: Path) -> dict[str, object]:
    """Parse package.json; return empty dict on any error."""
    p = mdir / "package.json"
    try:
        data: dict[str, object] = json.loads(p.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def _save_flag_for(name: str, pkg_data: dict[str, object]) -> list[str]:
    """Return the npm --save-* flags appropriate for *name*'s section.

    Detects the dep section (dependencies/devDependencies/optionalDependencies)
    and whether the current range is exact (no ``^``/``~`` prefix), then
    returns the matching combination of ``--save``, ``--save-dev``,
    ``--save-optional``, and ``--save-exact``.
    """
    flags: list[str] = []
    section_flags = {
        "devDependencies": ["--save-dev"],
        "optionalDependencies": ["--save-optional"],
        "peerDependencies": ["--save-peer"],
        "dependencies": ["--save"],
    }
    current_spec: str | None = None
    for section, sflag in section_flags.items():
        deps = pkg_data.get(section)
        if isinstance(deps, dict) and name in deps:
            flags = sflag
            current_spec = str(deps[name])
            break
    if not flags:
        # Package not found in any tracked section; default to --save.
        flags = ["--save"]
    # Preserve exact pins: no leading ^/~ means the range is exact.
    if current_spec is not None:
        stripped = current_spec.strip()
        if stripped and stripped[0] not in ("^", "~", ">", "<", "=", "*"):
            flags = [*flags, "--save-exact"]
    return flags


def targeted_bump_behind(
    ws: Workspace, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Per-package ``npm install <pkg>@latest`` fallback for still-behind deps (#122).

    Mirrors yarn's ``targeted_bump_behind``: for each behind-latest direct dep we
    snapshot the workspace's lockfile/manifests, run a targeted
    ``npm install <flags> <pkg>@latest``, and keep the bump only if install
    succeeds **and** the resolved version in node_modules actually advanced
    (guards the ghost-bump case where the lock churns but the resolver holds the
    old version due to an override/exact-pin). On any failure or no-op we restore
    the snapshot, leaving that package behind.

    Opt-in (``REPO_OPS_NPM_TARGETED_BUMP``) and reversible per-package: a bump
    that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version
    landed.
    """
    if not behind or not _npm_targeted_bump_enabled():
        return [], behind
    pkg_data = _read_pkg_json(ws.path)
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        snap = snapshot_workspace_files(ws.path, "npm")
        save_flags = _save_flag_for(change.name, pkg_data)
        install = run(
            ["npm", "install", *save_flags, f"{change.name}@latest"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        landed: str | None = None
        if install.returncode == 0:
            landed = resolved_version_from_node_modules(ws.path, change.name)
        advanced = False
        if landed:
            try:
                advanced = semver_tuple(landed) > semver_tuple(change.from_ver)
            except (TypeError, ValueError):
                advanced = landed != change.from_ver
        if landed and advanced and install.returncode == 0:
            applied.append(Change(change.name, change.from_ver, landed))
            assert_targets_resolved(
                ws, [Change(change.name, change.from_ver, landed)], manager="npm"
            )
        else:
            restore_workspace_snapshot(ws.path, snap, "npm")
            still_behind.append(change)
    return applied, still_behind


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
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        snippet = proc.stdout[:200].strip()
        print(
            f"WARN: npm outdated returned non-JSON output: {snippet!r}",
            file=sys.stderr,
        )
        return [], []
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


def apply_npm(ws: Workspace) -> list[Change]:
    """Apply npm upgrades; returns still-behind deps after optional targeted bumps.

    With ``REPO_OPS_NPM_TARGETED_BUMP`` set, each dep that ``npm update`` left
    behind_latest gets a reversible targeted ``npm install <pkg>@latest``; only
    bumps that actually advance the resolved version are kept. Returns the subset
    of behind_latest that could not be moved (empty in conservative mode or when
    all behind deps were successfully targeted).
    """
    changes, behind = check_npm(ws)
    proc = run(["npm", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "npm update failed")
    proc2 = run(["npm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "npm install failed")
    if changes:
        assert_targets_resolved(ws, changes, manager="npm")
    _, behind = targeted_bump_behind(ws, behind)
    return behind
