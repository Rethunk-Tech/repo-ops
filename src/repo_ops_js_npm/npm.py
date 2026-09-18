"""npm upgrade check/apply."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run
from repo_ops_core.version_utils import version_satisfies_ceiling
from repo_ops_js.guards import (
    assert_targets_resolved,
    fold_blocked_into_behind,
    unresolved_targets,
)
from repo_ops_js.holds import (
    direct_dependency_names,
    enforce_holds,
    fold_held_into_behind,
    resolve_package_hold,
)
from repo_ops_js.overrides import release_unheld_pin
from repo_ops_js.targeted_bump import run_targeted_bump
from repo_ops_js.workspaces import (
    apply_with_members,
    check_with_members,
    member_workspaces,
    merge_changes,
)
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules
from repo_ops_models import Change, Workspace


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
    ws: Workspace, behind: list[Change], *, root: Path | None = None
) -> tuple[list[Change], list[Change]]:
    """Per-package ``npm install <pkg>@latest`` fallback for still-behind deps.

    Mirrors yarn's ``targeted_bump_behind``: for each behind-latest direct dep we
    snapshot the workspace's lockfile/manifests, run a targeted
    ``npm install <flags> <pkg>@latest``, and keep the bump only if install
    succeeds **and** the resolved version in node_modules actually advanced
    (guards the ghost-bump case where the lock churns but the resolver holds the
    old version due to an override/exact-pin). On any failure or no-op we restore
    the snapshot, leaving that package behind.

    Reversible per-package: a bump that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version
    landed.
    """
    if not behind:
        return [], behind
    # The lockfile and hoisted node_modules live at the monorepo root; the
    # install runs in the member so it saves into the member's manifest.
    mdir = root or ws.path
    pkg_data = read_json_file(ws.path / "package.json") or {}

    def run_bump(name: str, version: str) -> bool:
        install = run(
            ["npm", "install", *_save_flag_for(name, pkg_data), f"{name}@{version}"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        return install.returncode == 0

    def resolve_landed(name: str) -> str | None:
        return resolved_version_from_node_modules(
            ws.path, name
        ) or resolved_version_from_node_modules(mdir, name)

    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        resolved = run_targeted_bump(
            ws,
            mdir,
            change,
            manager="npm",
            run_bump=run_bump,
            resolve_landed=resolve_landed,
            release_pin=release_unheld_pin,
            verify=assert_targets_resolved,
        )
        if resolved is None:
            still_behind.append(change)
        else:
            applied.append(resolved)
    return applied, still_behind


def _npm_outdated(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Parse ``npm outdated --json`` into ``(changes, behind_latest)``.

    Shared by :func:`check_npm` (plan, may promote behind) and :func:`apply_npm`
    (always uses the raw split so the ghost-bump guard stays correct).
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
    # At a monorepo root npm also lists every member's deps; those are checked and
    # bumped from their member directory so the bump saves into that manifest.
    root_names = (
        direct_dependency_names(read_json_file(ws.path / "package.json") or {})
        if member_workspaces(ws)
        else None
    )
    changes: list[Change] = []
    behind_latest: list[Change] = []
    for name, meta in data.items():
        if root_names is not None and name not in root_names:
            continue
        # A dep declared by several workspaces is a list, one entry per dependent.
        for entry in meta if isinstance(meta, list) else [meta]:
            if not isinstance(entry, dict):
                continue
            cur = str(entry.get("current", "?"))
            wanted = str(entry.get("wanted", "?"))
            latest = str(entry.get("latest", "?"))
            if cur != wanted and wanted not in ("?", ""):
                # In-range upgrade available.
                merge_changes(changes, [Change(name, cur, wanted)])
            elif latest not in ("?", "") and cur != latest:
                # latest exceeds current but wanted == current (out-of-range).
                merge_changes(behind_latest, [Change(name, cur, latest)])
    return changes, behind_latest


def check_npm(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check the root and every workspace member (see :func:`_check_npm_dir`)."""
    return check_with_members(ws, _check_npm_dir)


def _check_npm_dir(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated npm packages in one project directory.

    Returns ``(changes, behind_latest)`` where ``changes`` contains packages
    with in-range upgrades (``wanted`` > ``current``) and ``behind_latest``
    holds packages whose ``latest`` exceeds ``current`` but the in-range
    ``wanted`` version equals ``current`` (i.e. only a range-breaking bump
    is available).

    Behind_latest candidates are promoted into ``changes`` for plan preview parity with yarn (which runs
    targeted bumps on an isolated copy during plan). Apply uses
    :func:`_npm_outdated` directly so this promotion does not affect guards.

    A package held below the reported ``latest`` (an active hold rule whose
    ceiling the reported version would exceed) is not promoted — it stays in
    the returned still-behind list instead, matching what the targeted-bump
    apply path will actually do.
    """
    changes, behind = _npm_outdated(ws)
    if behind:
        promoted: list[Change] = []
        still_behind: list[Change] = []
        for c in behind:
            held_rule = resolve_package_hold(ws, c.name)
            if held_rule is not None and not version_satisfies_ceiling(c.to, held_rule.max_version):
                still_behind.append(c)
                continue
            promoted.append(c)
        for c in promoted:
            if not any(existing.name == c.name for existing in changes):
                changes.append(c)
        return changes, still_behind
    return changes, behind


def apply_npm(ws: Workspace) -> list[Change]:
    """Apply at the root and in every workspace member (see :func:`_apply_npm_dir`)."""
    return apply_with_members(ws, _apply_npm_dir)


def _apply_npm_dir(ws: Workspace, root: Path) -> list[Change]:
    """Apply npm upgrades in one directory; returns still-behind deps after targeted bumps.

    Each dep that ``npm update`` left behind_latest gets a reversible targeted
    ``npm install <pkg>@latest``; only bumps that actually advance the resolved
    version are kept. Returns the subset of behind_latest that could not be moved.
    """
    # Raw outdated split — do not use check_npm (plan may promote behind→changes).
    changes, behind = _npm_outdated(ws)
    proc = run(["npm", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "npm update failed")
    proc2 = run(["npm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "npm install failed")
    if changes:
        blocked = unresolved_targets(ws, changes)
        behind = fold_blocked_into_behind(blocked, behind)
    applied, behind = targeted_bump_behind(ws, behind, root=root)
    candidate_names = direct_dependency_names(read_json_file(ws.path / "package.json") or {}) | {
        c.name for c in (*changes, *applied, *behind)
    }
    held = enforce_holds(
        ws,
        candidate_names,
        manager="npm",
        run_install=lambda name, spec: run(
            ["npm", "install", f"{name}@{spec}"], ws.path, timeout=TIMEOUT_APPLY
        ),
    )
    return fold_held_into_behind(held, behind)
