"""bun upgrade check/apply."""

from __future__ import annotations

import json
import os
import re

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_lockfiles.snapshots import (
    resolved_version_from_node_modules,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace

# `bun outdated` has no --json flag (as of bun 1.x; the codebase checks
# opportunistically via check_bun but falls back here when JSON is absent).
# It always prints a table led by a `bun outdated vX (hash)` banner.  Parse
# that table row-by-row rather than whitespace-splitting (which mis-read the
# banner as a package named "bun" at version "(hash)" and fed
# `bun update bun@(hash)` → hard failure).
#
# The column separator depends on bun's terminal capability detection: Unicode
# box drawing (`│`) on a UTF-8-capable terminal, plain ASCII pipes (`|`)
# otherwise — CI runners and minimal containers get the ASCII table (#145).
# Both dialects share the same cell structure, so rows are split on whichever
# separator leads the line.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_BOX_V = "│"  # │ box drawings light vertical (column separator)
_ASCII_V = "|"  # ASCII fallback separator on non-UTF-8 terminals (#145)
_DEP_GROUP_SUFFIX_RE = re.compile(r"\s*\((?:dev|peer|optional)\)\s*$")
_PKG_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$")
_BUN_UPDATE_LATEST_ENV = "REPO_OPS_BUN_UPDATE_LATEST"
_BUN_TARGETED_BUMP_ENV = "REPO_OPS_BUN_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")

# Audit version overrides: package → advisory-safe version to use instead of
# the check_bun-derived (range-respecting) target. Populated by
# repo_ops_audit.audit.apply_remediations before the orchestration apply call
# and cleared in a finally block after (mirrors repo_ops_js_npm.npm's
# _AUDIT_VERSION_OVERRIDES). Not thread-safe, but apply_remediations processes
# workspaces sequentially. Empty outside of audit --fix runs.
_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}


def set_bun_audit_version_overrides(overrides: dict[str, str]) -> None:
    """Inject per-package advisory-safe versions for the next ``apply_bun`` call.

    Called by ``repo_ops_audit.audit.apply_remediations`` so that ``apply_bun``
    targets the advisory-safe version (e.g. ``pkg@1.5.5``) rather than silently
    dropping it when ``bun outdated`` doesn't report the package as changed
    (#140).
    """
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = dict(overrides)


def clear_bun_audit_version_overrides() -> None:
    """Reset audit version overrides after each workspace apply."""
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = {}


def _bun_update_latest_enabled() -> bool:
    return os.environ.get(_BUN_UPDATE_LATEST_ENV, "").strip().lower() in _TRUE_VALUES


def _bun_targeted_bump_enabled() -> bool:
    return (
        os.environ.get(_BUN_TARGETED_BUMP_ENV, "").strip().lower() in _TRUE_VALUES
    )


def _valid_bun_package_name(name: str) -> bool:
    n = (name or "").strip()
    # Reject only the table header (``Package``) and dash-led junk. A real npm
    # dependency literally named ``bun`` is valid: the ``bun outdated`` banner is
    # filtered by STRUCTURE (it is not a ``│``-delimited row) in
    # ``_changes_from_bun_text``, not by excluding the name ``bun`` here (#67).
    if not n or n.startswith("-") or n == "Package":
        return False
    return bool(_PKG_NAME_RE.match(n))


def _changes_from_bun_json(stdout: str) -> tuple[list[Change], list[Change]]:
    """Forward-compatible parser for a future `bun outdated --json`.

    Range-respecting by default (``update`` field); ``latest`` when opted in.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` contains
    packages that have a newer ``latest`` version available but are not
    actionable because latest-mode is off (and the in-range update equals
    the current version).
    """
    want_latest = _bun_update_latest_enabled()
    changes: list[Change] = []
    behind_latest: list[Change] = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return changes, behind_latest
    if not isinstance(data, dict):
        return changes, behind_latest
    for name, meta in data.items():
        if not _valid_bun_package_name(str(name)) or not isinstance(meta, dict):
            continue
        cur = str(meta.get("current") or meta.get("installed") or "?")
        latest = str(meta.get("latest", "?"))
        if want_latest:
            target = latest
        else:
            target = str(meta.get("update") or meta.get("latest") or "?")
        if cur != target and target not in ("?", ""):
            changes.append(Change(str(name), cur, target))
        elif not want_latest and latest not in ("?", "") and cur != latest:
            # In-range is already current, but a newer latest exists.
            behind_latest.append(Change(str(name), cur, latest))
    return changes, behind_latest


def _changes_from_bun_text(stdout: str) -> tuple[list[Change], list[Change]]:
    """Parse ``bun outdated``'s table (Unicode box drawing or ASCII pipes).

    Columns: ``Package │ Current │ Update │ Latest``. ``Update`` is the newest
    version within the package.json range; ``Latest`` ignores the range. Only
    separator-delimited data rows are read, so the banner, the ``Resolving…``
    line, the box borders, and the ``Package`` header are all skipped.  On
    non-UTF-8 terminals (CI runners, minimal containers) bun renders the same
    table with ASCII ``|`` separators (#145); ASCII border rows
    (``|-----|-----|``) split into dash-led cells that
    ``_valid_bun_package_name`` rejects.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` contains
    packages that have a newer ``latest`` version available but are not
    actionable because latest-mode is off (and the in-range update equals
    the current version).
    """
    want_latest = _bun_update_latest_enabled()
    changes: list[Change] = []
    behind_latest: list[Change] = []
    for raw in stdout.splitlines():
        line = _ANSI_RE.sub("", raw).strip()
        if line.startswith(_BOX_V):
            sep = _BOX_V
        elif line.startswith(_ASCII_V):
            sep = _ASCII_V
        else:
            continue
        # "│ name │ current │ update │ latest │" -> ['', name, cur, upd, lat, '']
        cells = [c.strip() for c in line.split(sep)]
        if len(cells) < 6:
            continue
        name = _DEP_GROUP_SUFFIX_RE.sub("", cells[1]).strip()
        if name == "Package" or not _valid_bun_package_name(name):
            continue
        current = cells[2]
        update_ver = cells[3]
        latest_ver = cells[4]
        target = latest_ver if want_latest else update_ver
        if not current or not target or current == target:
            # When latest-mode is off, check if there's a newer latest to report.
            if not want_latest and latest_ver and current and current != latest_ver:
                behind_latest.append(Change(name, current, latest_ver))
            continue
        changes.append(Change(name, current, target))
    return changes, behind_latest


def check_bun(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated bun packages.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` holds packages
    that have a newer ``latest`` version available but are not actioned when
    ``REPO_OPS_BUN_UPDATE_LATEST`` is unset.
    """
    proc_json = run(
        ["bun", "outdated", "--json"], ws.path, timeout=resolve_timeout_check()
    )
    if proc_json.returncode in (0, 1) and proc_json.stdout.strip():
        try:
            data = json.loads(proc_json.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return _changes_from_bun_json(proc_json.stdout)

    proc = run(["bun", "outdated"], ws.path, timeout=resolve_timeout_check())
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed"
        )
    return _changes_from_bun_text(proc.stdout)


def targeted_bump_behind(
    ws: Workspace, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Per-package ``bun add <pkg>@<version>`` fallback for still-behind deps.

    Mirrors npm's ``targeted_bump_behind``: for each behind-latest direct dep we
    snapshot the workspace's lockfile/manifests, run a targeted
    ``bun add <pkg>@<version>``, and keep the bump only if install succeeds
    **and** the resolved version in node_modules actually advanced (guards the
    ghost-bump case where the lock churns but the resolver holds the old
    version due to an override/exact-pin). On any failure or no-op we restore
    the snapshot, leaving that package behind.

    Opt-in (``REPO_OPS_BUN_TARGETED_BUMP``) and reversible per-package: a bump
    that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version
    landed.
    """
    if not behind or not _bun_targeted_bump_enabled():
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        snap = snapshot_workspace_files(ws.path, "bun")
        target_ver = _AUDIT_VERSION_OVERRIDES.get(change.name, "latest")
        install = run(
            ["bun", "add", f"{change.name}@{target_ver}"],
            ws.path,
            timeout=resolve_timeout_apply(),
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
                ws, [Change(change.name, change.from_ver, landed)], manager="bun"
            )
        else:
            restore_workspace_snapshot(ws.path, snap, "bun")
            still_behind.append(change)
    return applied, still_behind


def apply_bun(ws: Workspace) -> list[Change]:
    """Apply upgrades reported by ``check_bun`` without rewriting ranges to ``latest``.

    Advisory-safe version overrides (``set_bun_audit_version_overrides``, wired
    by ``repo_ops_audit.audit.apply_remediations`` during ``audit --fix``) are
    applied independent of ``check_bun``'s findings: an overridden package is
    targeted at its safe version even when ``bun outdated`` reports it as
    already in-range (empty ``changes``) or only behind on ``latest`` — dropping
    it in that case was the #140 silent-skip bug.

    With ``REPO_OPS_BUN_TARGETED_BUMP`` set, each dep that ``check_bun`` left
    behind_latest gets a reversible targeted ``bun add <pkg>@latest``; only
    bumps that actually advance the resolved version are kept. Returns the
    subset of behind_latest that could not be moved (empty when the flag is
    off or all behind deps were successfully targeted).
    """
    changes, behind = check_bun(ws)
    targets: list[str] = []
    requested: list[Change] = []
    seen_names: set[str] = set()
    for ch in changes:
        name = (ch.name or "").strip()
        if not name or not _valid_bun_package_name(name):
            continue
        version = _AUDIT_VERSION_OVERRIDES.get(name) or (ch.to or "").strip()
        if not version or version in ("?", "latest", "|"):
            continue
        targets.append(f"{name}@{version}")
        requested.append(Change(name, ch.from_ver, version))
        seen_names.add(name)
    # Advisory-safe overrides for packages check_bun did NOT report as changed
    # (already satisfies its declared range, or only behind_latest) must still
    # be targeted explicitly here — silently dropping them is the #140 bug.
    for name, safe_version in _AUDIT_VERSION_OVERRIDES.items():
        name = (name or "").strip()
        safe_version = (safe_version or "").strip()
        if (
            not name
            or name in seen_names
            or not safe_version
            or not _valid_bun_package_name(name)
        ):
            continue
        targets.append(f"{name}@{safe_version}")
        requested.append(Change(name, "", safe_version))
        seen_names.add(name)
    if not changes and not targets:
        _, behind = targeted_bump_behind(ws, behind)
        return behind
    if not targets:
        # Bare ``bun update`` — no per-package target args; the guard is still
        # applied to any changes whose target version IS known (task #3: skip
        # unparseable '?' entries but guard what IS known from the original
        # check output).
        proc = run(["bun", "update"], ws.path, timeout=resolve_timeout_apply())
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "bun update failed")
        # Re-check original changes for known target versions.
        guardable = [
            ch
            for ch in changes
            if (ch.to or "").strip() not in ("", "?", "latest", "|")
            and _valid_bun_package_name((ch.name or "").strip())
        ]
        if guardable:
            assert_targets_resolved(ws, guardable, manager="bun")
        _, behind = targeted_bump_behind(ws, behind)
        return behind
    proc = run(
        ["bun", "update", "--", *targets], ws.path, timeout=resolve_timeout_apply()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")
    assert_targets_resolved(ws, requested, manager="bun")
    _, behind = targeted_bump_behind(ws, behind)
    return behind
