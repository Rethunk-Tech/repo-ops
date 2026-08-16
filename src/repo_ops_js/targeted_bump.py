"""Shared behind-latest single-package retry (#122/#111).

Every JS package manager runs the same reversible retry when a bulk update
leaves a direct dependency behind: snapshot, pick a target version, release
any legacy pin, run the manager-specific bump, and keep it only if the
resolved version actually advanced past ``change.from_ver``. Manager-specific
mechanics — save-flag detection, an isolated-copy directory, a lockfile vs
``node_modules`` read, a catalog rewrite — stay with the caller via closures.

``release_pin`` and ``verify`` are passed in rather than imported here so each
caller's own module-level reference is what actually runs — tests patch
``repo_ops_js_npm.npm.release_unheld_pin`` / ``...assert_targets_resolved``
(and the bun/pnpm equivalents) expecting that patch to take effect.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from repo_ops_core.version_utils import semver_tuple
from repo_ops_js.holds import hold_version_spec, resolve_package_hold
from repo_ops_lockfiles.snapshots import (
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace


def run_targeted_bump(
    ws: Workspace | None,
    mdir: Path,
    change: Change,
    *,
    manager: str,
    audit_overrides: dict[str, str],
    run_bump: Callable[[str, str], bool],
    resolve_landed: Callable[[str], str | None],
    release_pin: Callable[[Path, str], object],
    verify: Callable[[Workspace, list[Change], str], None] | None,
    release_pin_on_audit_override: bool = True,
) -> Change | None:
    """Attempt one behind-latest bump; return the landed ``Change`` or ``None``.

    Target precedence: an advisory-safe audit override (a security
    remediation must not be silently blocked by a routine hold) wins over an
    active hold ceiling, which wins over registry latest. On any failure,
    no-op, or ghost bump (the resolved version didn't actually advance, or
    *verify* still finds it unresolved) the snapshot is restored and ``None``
    is returned so the caller keeps *change* in its behind list.

    ``release_pin_on_audit_override`` exists only for pnpm, which leaves a
    legacy pin in place when an audit override is active; every other
    manager releases it there too, same as when no hold is active at all.

    ``verify`` re-checks the landed version against ``node_modules`` before
    trusting it; pass ``None`` to skip (yarn: its own *resolve_landed* already
    reads the freshly-written lockfile, a different — and for Plug'n'Play
    workspaces, absent — source of truth).
    """
    snap = snapshot_workspace_files(mdir, manager)
    audit_override = audit_overrides.get(change.name)
    held_rule = None if audit_override or ws is None else resolve_package_hold(ws, change.name)
    if audit_override:
        target_ver = audit_override
    elif held_rule is not None:
        target_ver = hold_version_spec(held_rule)
    else:
        target_ver = "latest"
    if held_rule is None and (release_pin_on_audit_override or not audit_override):
        release_pin(mdir, change.name)
    ok = run_bump(change.name, target_ver)
    landed = resolve_landed(change.name) if ok else None
    advanced = False
    if landed:
        try:
            advanced = semver_tuple(landed) > semver_tuple(change.from_ver)
        except (TypeError, ValueError):
            advanced = landed != change.from_ver
    if landed and advanced:
        resolved_change = Change(change.name, change.from_ver, landed)
        if verify is not None and ws is not None:
            try:
                verify(ws, [resolved_change], manager)
            except RuntimeError:
                # Ghost bump: the guard itself found the target unresolved
                # (e.g. a second read disagrees with the first). Restore the
                # snapshot instead of leaving a corrupted lockfile on disk.
                restore_workspace_snapshot(mdir, snap, manager)
                return None
        return resolved_change
    restore_workspace_snapshot(mdir, snap, manager)
    return None
