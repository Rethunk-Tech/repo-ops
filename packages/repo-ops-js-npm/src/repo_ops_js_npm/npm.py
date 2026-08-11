"""npm upgrade check/apply."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_js.guards import assert_targets_resolved, unresolved_targets
from repo_ops_js.holds import (
    direct_dependency_names,
    enforce_holds,
    hold_version_spec,
    resolve_package_hold,
)
from repo_ops_js.overrides import release_unheld_pin
from repo_ops_lockfiles.snapshots import (
    resolved_version_from_node_modules,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace

_NPM_TARGETED_BUMP_ENV = "REPO_OPS_NPM_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")

# Audit version overrides: package → advisory-safe version to use instead of @latest.
# Populated by repo_ops_audit.audit.apply_remediations before the orchestration apply
# call and cleared in a finally block after.  Empty outside of audit --fix runs.
# Not thread-safe, but apply_remediations processes workspaces sequentially.
_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}


def set_npm_audit_version_overrides(overrides: dict[str, str]) -> None:
    """Inject per-package advisory-safe versions for the next apply / targeted-bump.

    Called by ``repo_ops_audit.audit.apply_remediations`` so that
    ``apply_npm`` / ``targeted_bump_behind`` uses the advisory-safe version
    (e.g. ``pkg@1.5.5``) rather than always falling back to ``pkg@latest``.
    """
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = dict(overrides)


def clear_npm_audit_version_overrides() -> None:
    """Reset audit version overrides after each workspace apply."""
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = {}


def _npm_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "npm_targeted_bump",
        cli_value=process_env_cli_override(_NPM_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUE_VALUES


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
    if not behind or not _npm_targeted_bump_enabled(ws):
        return [], behind
    pkg_data = _read_pkg_json(ws.path)
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        snap = snapshot_workspace_files(ws.path, "npm")
        save_flags = _save_flag_for(change.name, pkg_data)
        # Use the advisory-safe version when set (audit --fix path) — a
        # security remediation must not be silently blocked by a routine
        # hold policy; else a hold ceiling when one is active for this
        # package/workspace; else @latest.
        audit_override = _AUDIT_VERSION_OVERRIDES.get(change.name)
        held_rule = None if audit_override else resolve_package_hold(ws, change.name)
        if audit_override:
            target_ver = audit_override
        elif held_rule is not None:
            target_ver = hold_version_spec(held_rule)
        else:
            target_ver = "latest"
        if held_rule is None:
            # No active hold covers this package — release a legacy
            # overrides pin so the retry has a real chance; the snapshot
            # restore below reverts this edit too on no-advance.
            release_unheld_pin(ws.path, change.name)
        install = run(
            ["npm", "install", *save_flags, f"{change.name}@{target_ver}"],
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
            resolved_change = Change(change.name, change.from_ver, landed)
            try:
                assert_targets_resolved(ws, [resolved_change], manager="npm")
            except RuntimeError:
                # Ghost bump: the guard itself found the target unresolved
                # (e.g. a second read disagrees with the first). Restore the
                # snapshot instead of leaving a corrupted lockfile on disk.
                restore_workspace_snapshot(ws.path, snap, "npm")
                still_behind.append(change)
            else:
                applied.append(resolved_change)
        else:
            restore_workspace_snapshot(ws.path, snap, "npm")
            still_behind.append(change)
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


def check_npm(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated npm packages.

    Returns ``(changes, behind_latest)`` where ``changes`` contains packages
    with in-range upgrades (``wanted`` > ``current``) and ``behind_latest``
    holds packages whose ``latest`` exceeds ``current`` but the in-range
    ``wanted`` version equals ``current`` (i.e. only a range-breaking bump
    is available).

    When ``REPO_OPS_NPM_TARGETED_BUMP`` is set, behind_latest candidates are
    promoted into ``changes`` for plan preview parity with yarn (which runs
    targeted bumps on an isolated copy during plan). Apply uses
    :func:`_npm_outdated` directly so this promotion does not affect guards.

    A package held below the reported ``latest`` (an active hold rule whose
    ceiling the reported version would exceed) is not promoted — it stays in
    the returned still-behind list instead, matching what the targeted-bump
    apply path will actually do.
    """
    changes, behind = _npm_outdated(ws)
    if _npm_targeted_bump_enabled(ws) and behind:
        promoted: list[Change] = []
        still_behind: list[Change] = []
        for c in behind:
            held_rule = resolve_package_hold(ws, c.name)
            if held_rule is not None and not version_satisfies_ceiling(
                c.to, held_rule.max_version
            ):
                still_behind.append(c)
                continue
            promoted.append(c)
        for c in promoted:
            if not any(existing.name == c.name for existing in changes):
                changes.append(c)
        return changes, still_behind
    return changes, behind


def _apply_npm_audit_overrides(ws: Workspace) -> set[str]:
    """Install advisory-safe pins from ``_AUDIT_VERSION_OVERRIDES`` (#140 / bun parity).

    Independent of ``REPO_OPS_NPM_TARGETED_BUMP`` so ``audit --fix`` pins are not
    silently skipped when the user did not also opt into targeted bump.
    """
    handled: set[str] = set()
    if not _AUDIT_VERSION_OVERRIDES:
        return handled
    pkg_data = _read_pkg_json(ws.path)
    requested: list[Change] = []
    for name, safe_version in list(_AUDIT_VERSION_OVERRIDES.items()):
        name = (name or "").strip()
        safe_version = (safe_version or "").strip()
        if not name or not safe_version:
            continue
        save_flags = _save_flag_for(name, pkg_data)
        install = run(
            ["npm", "install", *save_flags, f"{name}@{safe_version}"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        if install.returncode != 0:
            raise RuntimeError(
                install.stderr.strip()
                or f"npm install {name}@{safe_version} failed (audit override)"
            )
        requested.append(Change(name, "", safe_version))
        handled.add(name)
    if requested:
        assert_targets_resolved(ws, requested, manager="npm")
    return handled


def apply_npm(ws: Workspace) -> list[Change]:
    """Apply npm upgrades; returns still-behind deps after optional targeted bumps.

    Advisory-safe version overrides (``set_npm_audit_version_overrides``, wired
    by ``repo_ops_audit.audit.apply_remediations`` during ``audit --fix``) are
    applied independent of ``check_npm``'s findings and of the targeted-bump
    flag — dropping them when outdated is empty was the silent-skip bug (#140).

    With ``REPO_OPS_NPM_TARGETED_BUMP`` set, each dep that ``npm update`` left
    behind_latest gets a reversible targeted ``npm install <pkg>@latest``; only
    bumps that actually advance the resolved version are kept. Returns the subset
    of behind_latest that could not be moved (the full behind list when targeted
    bump is unset, or the remaining gaps after targeted bump runs).
    """
    # Raw outdated split — do not use check_npm (plan may promote behind→changes).
    changes, behind = _npm_outdated(ws)
    handled = _apply_npm_audit_overrides(ws)
    if handled:
        changes = [c for c in changes if c.name not in handled]
        behind = [c for c in behind if c.name not in handled]
    # Preserve prior always-update behavior when no audit overrides were applied;
    # when ONLY overrides were applied and there are no remaining in-range
    # changes, skip the bare npm update (bun parity).
    if changes or not handled:
        proc = run(["npm", "update"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "npm update failed")
        proc2 = run(["npm", "install"], ws.path, timeout=TIMEOUT_APPLY)
        if proc2.returncode != 0:
            raise RuntimeError(proc2.stderr.strip() or "npm install failed")
        if changes:
            if _npm_targeted_bump_enabled(ws):
                blocked = unresolved_targets(ws, changes)
                behind_names = {c.name for c in behind}
                for change, _resolved in blocked:
                    if change.name not in behind_names:
                        behind.append(change)
                        behind_names.add(change.name)
            else:
                assert_targets_resolved(ws, changes, manager="npm")
    applied, behind = targeted_bump_behind(ws, behind)
    candidate_names = direct_dependency_names(_read_pkg_json(ws.path)) | {
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
    if held:
        behind = [c for c in behind if c.name not in {h.name for h in held}] + held
    return behind
