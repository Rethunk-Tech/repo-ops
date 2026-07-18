"""pnpm upgrade check/apply."""

from __future__ import annotations

import json
import os
import sys

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

_PNPM_UPDATE_LATEST_ENV = "REPO_OPS_PNPM_UPDATE_LATEST"
_PNPM_TARGETED_BUMP_ENV = "REPO_OPS_PNPM_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")

# Audit version overrides: package → advisory-safe version to use instead of @latest.
# Populated by repo_ops_audit.audit.apply_remediations before the orchestration apply
# call and cleared in a finally block after.  Empty outside of audit --fix runs.
_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}


def set_pnpm_audit_version_overrides(overrides: dict[str, str]) -> None:
    """Inject per-package advisory-safe versions for the next apply / targeted-bump.

    Called by ``repo_ops_audit.audit.apply_remediations`` so that
    ``apply_pnpm`` / ``targeted_bump_behind`` uses the advisory-safe version
    (e.g. ``pnpm add pkg@1.5.5``) rather than always running
    ``pnpm update --latest pkg``.
    """
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = dict(overrides)


def clear_pnpm_audit_version_overrides() -> None:
    """Reset audit version overrides after each workspace apply."""
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = {}


def _pnpm_update_latest_enabled() -> bool:
    return os.environ.get(_PNPM_UPDATE_LATEST_ENV, "").strip().lower() in _TRUE_VALUES


def _pnpm_targeted_bump_enabled() -> bool:
    return os.environ.get(_PNPM_TARGETED_BUMP_ENV, "").strip().lower() in _TRUE_VALUES


def _bucket_pnpm_pkg(
    name: str,
    cur: str,
    wanted: str,
    latest: str,
    changes: list[Change],
    behind_latest: list[Change],
) -> None:
    """Bucket a single pnpm package into changes or behind_latest.

    ``wanted`` is the newest version that satisfies the declared range;
    ``latest`` is the registry's latest release (may break the range).

    When ``REPO_OPS_PNPM_UPDATE_LATEST`` is set, ``latest`` is used as the
    target so packages that are only behind latest are treated as actionable
    (matching what ``apply_pnpm`` does when it passes ``--latest``).
    """
    if _pnpm_update_latest_enabled():  # noqa: SIM102
        # Treat latest as the upgrade target — mirrors apply_pnpm --latest.
        if latest not in ("?", "") and cur != latest:
            changes.append(Change(name, cur, latest))
            return
        # latest unknown ('?') — fall through to wanted check so a known
        # in-range bump is not silently omitted (#5).
    if cur != wanted and wanted not in ("?", ""):
        changes.append(Change(name, cur, wanted))
    elif latest not in ("?", "") and cur != latest:
        behind_latest.append(Change(name, cur, latest))


def _pnpm_outdated(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Parse ``pnpm outdated`` into ``(changes, behind_latest)``.

    Shared by :func:`check_pnpm` (plan, may promote behind) and :func:`apply_pnpm`
    (always uses the raw split so the ghost-bump guard stays correct).
    """
    proc = run(["pnpm", "outdated", "--format", "json"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "pnpm outdated failed")
    changes: list[Change] = []
    behind_latest: list[Change] = []
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
            items = data if isinstance(data, list) else data.get("packages", data)
            if isinstance(items, dict):
                for name, meta in items.items():
                    if isinstance(meta, dict):
                        cur = str(meta.get("current", "?"))
                        wanted = str(meta.get("wanted", "?"))
                        latest = str(meta.get("latest", "?"))
                        _bucket_pnpm_pkg(
                            name, cur, wanted, latest, changes, behind_latest
                        )
            elif isinstance(items, list):
                for meta in items:
                    if isinstance(meta, dict) and meta.get("name"):
                        cur = str(meta.get("current", "?"))
                        wanted = str(meta.get("wanted", "?"))
                        latest = str(meta.get("latest", "?"))
                        _bucket_pnpm_pkg(
                            str(meta["name"]),
                            cur,
                            wanted,
                            latest,
                            changes,
                            behind_latest,
                        )
        except json.JSONDecodeError:
            snippet = proc.stdout[:200].strip()
            print(
                f"WARN: pnpm outdated returned non-JSON output: {snippet!r}",
                file=sys.stderr,
            )
    return changes, behind_latest


def check_pnpm(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated pnpm packages.

    Returns ``(changes, behind_latest)`` where ``changes`` contains packages
    with in-range upgrades and ``behind_latest`` holds packages whose
    ``latest`` exceeds ``current`` but the in-range ``wanted`` equals
    ``current`` (only a range-breaking bump is available).

    When ``REPO_OPS_PNPM_TARGETED_BUMP`` is set, behind_latest candidates are
    promoted into ``changes`` for plan preview parity with yarn. Apply uses
    :func:`_pnpm_outdated` directly so this promotion does not affect guards.
    """
    changes, behind = _pnpm_outdated(ws)
    if _pnpm_targeted_bump_enabled() and behind:
        for c in behind:
            if not any(existing.name == c.name for existing in changes):
                changes.append(c)
        return changes, []
    return changes, behind


def targeted_bump_behind(
    ws: Workspace, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Per-package ``pnpm update --latest <pkg>`` fallback for still-behind deps (#122).

    Mirrors yarn's ``targeted_bump_behind``: for each behind-latest direct dep we
    snapshot the workspace's lockfile/manifests, run a targeted
    ``pnpm update --latest <pkg>`` (no ``-r``, single-package scope), and keep
    the bump only if install succeeds **and** the resolved version in node_modules
    actually advanced (guards the ghost-bump case where the lock churns but the
    resolver holds the old version due to an override/resolution entry or a
    peer/group constraint). On any failure or no-op we restore the snapshot,
    leaving that package behind.

    ``pnpm update --latest <pkg>`` (without ``-r``) targets only the named
    package, updates both ``pnpm-lock.yaml`` and the manifest range entry, and
    exits non-zero on install failure — making it structurally equivalent to
    ``yarn up <pkg>@latest`` + ``yarn install``.

    Opt-in (``REPO_OPS_PNPM_TARGETED_BUMP``) and reversible per-package: a bump
    that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version
    landed.
    """
    if not behind or not _pnpm_targeted_bump_enabled():
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        snap = snapshot_workspace_files(ws.path, "pnpm")
        target_ver = _AUDIT_VERSION_OVERRIDES.get(change.name)
        if target_ver:
            # Audit path: pin to advisory-safe version via pnpm add pkg@version.
            # pnpm add installs an explicit version and updates both manifest and lockfile.
            cmd = ["pnpm", "add", f"{change.name}@{target_ver}"]
        else:
            # Normal targeted-bump path: update to registry latest.
            cmd = ["pnpm", "update", "--latest", change.name]
        update = run(cmd, ws.path, timeout=TIMEOUT_APPLY)
        landed: str | None = None
        if update.returncode == 0:
            landed = resolved_version_from_node_modules(ws.path, change.name)
        advanced = False
        if landed:
            try:
                advanced = semver_tuple(landed) > semver_tuple(change.from_ver)
            except (TypeError, ValueError):
                advanced = landed != change.from_ver
        if landed and advanced and update.returncode == 0:
            resolved_change = Change(change.name, change.from_ver, landed)
            try:
                assert_targets_resolved(ws, [resolved_change], manager="pnpm")
            except RuntimeError:
                # Ghost bump: guard raise must restore the snapshot (npm/bun parity).
                restore_workspace_snapshot(ws.path, snap, "pnpm")
                still_behind.append(change)
            else:
                applied.append(resolved_change)
        else:
            restore_workspace_snapshot(ws.path, snap, "pnpm")
            still_behind.append(change)
    return applied, still_behind


def _apply_pnpm_audit_overrides(ws: Workspace) -> set[str]:
    """Install advisory-safe pins from ``_AUDIT_VERSION_OVERRIDES`` (#140 / bun parity).

    Independent of ``REPO_OPS_PNPM_TARGETED_BUMP`` so ``audit --fix`` pins are not
    silently skipped when the user did not also opt into targeted bump.
    """
    handled: set[str] = set()
    if not _AUDIT_VERSION_OVERRIDES:
        return handled
    requested: list[Change] = []
    for name, safe_version in list(_AUDIT_VERSION_OVERRIDES.items()):
        name = (name or "").strip()
        safe_version = (safe_version or "").strip()
        if not name or not safe_version:
            continue
        add = run(
            ["pnpm", "add", f"{name}@{safe_version}"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        if add.returncode != 0:
            raise RuntimeError(
                add.stderr.strip()
                or f"pnpm add {name}@{safe_version} failed (audit override)"
            )
        requested.append(Change(name, "", safe_version))
        handled.add(name)
    if requested:
        assert_targets_resolved(ws, requested, manager="pnpm")
    return handled


def apply_pnpm(ws: Workspace) -> list[Change]:
    """Apply pnpm upgrades; returns still-behind deps after optional targeted bumps.

    Advisory-safe version overrides (``set_pnpm_audit_version_overrides``, wired
    by ``repo_ops_audit.audit.apply_remediations`` during ``audit --fix``) are
    applied independent of ``check_pnpm``'s findings and of the targeted-bump
    flag — dropping them when outdated is empty was the silent-skip bug (#140).

    With ``REPO_OPS_PNPM_TARGETED_BUMP`` set, each dep that ``pnpm update`` left
    behind_latest gets a reversible targeted ``pnpm update --latest <pkg>``; only
    bumps that actually advance the resolved version are kept. Returns the subset
    of behind_latest that could not be moved (the full behind list when targeted
    bump is unset, or the remaining gaps after targeted bump runs).
    """
    # Raw outdated split — do not use check_pnpm (plan may promote behind→changes).
    changes, behind = _pnpm_outdated(ws)
    handled = _apply_pnpm_audit_overrides(ws)
    if handled:
        changes = [c for c in changes if c.name not in handled]
        behind = [c for c in behind if c.name not in handled]
    if changes or not handled:
        # Match npm conservative bumps (semver ranges) unless the user opts into latest.
        cmd = ["pnpm", "update", "-r"]
        if _pnpm_update_latest_enabled():
            cmd.append("--latest")
        proc = run(cmd, ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "pnpm update failed")
        proc2 = run(["pnpm", "install"], ws.path, timeout=TIMEOUT_APPLY)
        if proc2.returncode != 0:
            raise RuntimeError(proc2.stderr.strip() or "pnpm install failed")
        if changes:
            assert_targets_resolved(ws, changes, manager="pnpm")
    _, behind = targeted_bump_behind(ws, behind)
    return behind
