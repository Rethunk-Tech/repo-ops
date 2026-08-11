"""Deno upgrade check/apply."""

from __future__ import annotations

import os
import re
import shutil
import sys
from functools import cache
from pathlib import Path

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_lockfiles.parsers import versions_deno_lock
from repo_ops_lockfiles.snapshots import (
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace

from repo_ops_js.guards import assert_targets_resolved

_DENO_UPDATE_LATEST_ENV = "REPO_OPS_DENO_UPDATE_LATEST"
_DENO_TARGETED_BUMP_ENV = "REPO_OPS_DENO_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")
_SEMVER_RE = re.compile(
    r"^[vV]?\d+(?:\.\d+)*(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _deno_update_latest_enabled() -> bool:
    return os.environ.get(_DENO_UPDATE_LATEST_ENV, "").strip().lower() in _TRUE_VALUES


def _deno_targeted_bump_enabled() -> bool:
    return os.environ.get(_DENO_TARGETED_BUMP_ENV, "").strip().lower() in _TRUE_VALUES


def _parseable_semver_tuple(version: str) -> tuple[int, ...] | None:
    if not _SEMVER_RE.fullmatch(version):
        return None
    return semver_tuple(version)


@cache
def _global_holds_cached() -> tuple[HoldRule, ...]:
    return tuple(load_global_holds())


@cache
def _repo_holds_cached(repo_root: str) -> tuple[HoldRule, ...]:
    return tuple(load_repo_holds(Path(repo_root)))


def _workspace_rel(ws: Workspace) -> str:
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve()).as_posix()
    except ValueError:
        return "."
    return rel or "."


def _resolve_deno_hold(ws: Workspace, package: str) -> HoldRule | None:
    """Return the active hold rule for *package* in this workspace, if any."""
    repo_root = str(ws.git_repo.resolve())
    rules = [*_global_holds_cached(), *_repo_holds_cached(repo_root)]
    if not rules:
        return None
    return resolve_hold(
        rules,
        package=package,
        ecosystem=ws.ecosystem,
        manager=ws.manager,
        workspace_rel=_workspace_rel(ws),
    )


def _filter_held_deno_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    """Drop changes whose target exceeds an active package hold."""
    return [
        change
        for change in changes
        if (
            (rule := _resolve_deno_hold(ws, change.name)) is None
            or version_satisfies_ceiling(change.to, rule.max_version)
        )
    ]


def check_deno(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Return pending upgrades for a Deno workspace.

    Runs ``deno outdated`` (Deno ≥ 2.x) and parses its tabular output.
    Supports both the box-drawing table format and plain-text column format::

        Package           Current  Update   Latest
        jsr:@std/assert   0.221.0  0.226.0  0.226.0
        npm:chalk         5.2.0    5.3.0    5.3.0

    Returns ``(changes, behind_latest)``. By default (conservative), ``Update``
    is the actionable target and packages where only ``Latest`` is newer go to
    ``behind_latest``. With ``REPO_OPS_DENO_UPDATE_LATEST`` set, ``Latest`` is
    the actionable target and ``behind_latest`` is empty.

    Falls back to empty lists with a WARN when ``deno`` is absent so the
    workspace degrades gracefully (report-only, no error).
    """
    if not shutil.which("deno"):
        print(f"WARN {ws.path}: deno not on PATH", file=sys.stderr)
        return [], []
    proc = run(
        ["deno", "outdated"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "deno outdated failed")
    changes, behind_latest = _parse_deno_outdated(proc.stdout)
    return (
        _filter_held_deno_changes(ws, changes),
        _filter_held_deno_changes(ws, behind_latest),
    )


def _parse_deno_outdated(stdout: str) -> tuple[list[Change], list[Change]]:
    """Parse ``deno outdated`` stdout into ``(changes, behind_latest)``."""
    want_latest = _deno_update_latest_enabled()
    changes: list[Change] = []
    behind_latest: list[Change] = []
    # Plain text: "name  current  update  latest" OR "name  current  latest"
    _PLAIN_4 = re.compile(r"^(\S+)\s+([\d][^\s]*)\s+([\d][^\s]*)\s+([\d][^\s]*)")
    _PLAIN_3 = re.compile(r"^(\S+)\s+([\d][^\s]*)\s+([\d][^\s]*)")
    # Vertical-bar character used in Deno's box-drawing table output.
    _VBAR = "│"  # │ (BOX DRAWINGS LIGHT VERTICAL)
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _VBAR in stripped:
            # Box-drawing table row: split on │ to get cell contents.
            # Border rows (┌─┬─┐, ├─┼─┤, └─┴─┘) do not contain │ so they
            # fall through to the plain-text branch which won't match them.
            parts = [p.strip() for p in stripped.split(_VBAR) if p.strip()]
            if len(parts) < 3:
                continue
            name = parts[0]
            if name.lower() in ("package", "name"):
                continue  # header row
            cur = parts[1]
            if len(parts) >= 4:
                update_ver, latest = parts[2], parts[3]
            else:
                # Legacy 3-column table: treat the last column as both Update and Latest.
                update_ver = latest = parts[-1]
            if not cur or not re.match(r"[\d]", cur):
                continue
            target = latest if want_latest else update_ver
            if target and cur != target:
                changes.append(Change(name, cur, target))
            elif not want_latest and latest and cur != latest and update_ver == cur:
                behind_latest.append(Change(name, cur, latest))
            continue
        m4 = _PLAIN_4.match(stripped)
        if m4:
            name, cur, update_ver, latest = (
                m4.group(1),
                m4.group(2),
                m4.group(3),
                m4.group(4),
            )
            if name.lower() in ("package", "name"):
                continue
            target = latest if want_latest else update_ver
            if cur != target:
                changes.append(Change(name, cur, target))
            elif not want_latest and cur != latest and update_ver == cur:
                behind_latest.append(Change(name, cur, latest))
            continue
        m = _PLAIN_3.match(stripped)
        if m:
            name, cur, latest = m.group(1), m.group(2), m.group(3)
            if name.lower() in ("package", "name") or cur == latest:
                continue
            # 3-column plain: no distinct Update — treat Latest as the only target.
            changes.append(Change(name, cur, latest))
    return changes, behind_latest


def _landed_deno_version(ws: Workspace, name: str) -> str | None:
    """Return the resolved lockfile version for a package name."""
    prefix = f"{name}@"
    versions = [
        resolved[len(prefix) :].strip()
        for resolved in versions_deno_lock(Path(ws.path) / "deno.lock")
        if resolved.startswith(prefix)
    ]
    versions = [version for version in versions if version]
    if not versions:
        return None
    parseable = [
        (parsed, version)
        for version in versions
        if (parsed := _parseable_semver_tuple(version)) is not None
    ]
    if parseable:
        return max(parseable, key=lambda item: item[0])[1]
    return max(versions)


def _deno_lock_package_names(ws: Workspace) -> set[str]:
    """Return package identities recorded in the resolved Deno lockfile."""
    names: set[str] = set()
    for resolved in versions_deno_lock(Path(ws.path) / "deno.lock"):
        name, separator, version = resolved.rpartition("@")
        if separator and name and version:
            names.add(name)
    return names


def targeted_bump_behind(
    ws: Workspace, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Run a reversible per-package ``deno update`` for behind-latest deps."""
    if not behind or not _deno_targeted_bump_enabled():
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        hold = _resolve_deno_hold(ws, change.name)
        if hold is not None and not version_satisfies_ceiling(
            change.to, hold.max_version
        ):
            still_behind.append(change)
            continue
        snapshot = snapshot_workspace_files(ws.path, "deno")
        cmd = ["deno", "update"]
        if _deno_update_latest_enabled():
            cmd.append("--latest")
        cmd.append(change.name)
        proc = run(cmd, ws.path, timeout=resolve_timeout_apply())
        advanced = False
        if proc.returncode == 0:
            advanced = snapshot_workspace_files(ws.path, "deno") != snapshot
        if advanced:
            landed = _landed_deno_version(ws, change.name)
            if landed is None:
                restore_workspace_snapshot(ws.path, snapshot, "deno")
                still_behind.append(change)
                continue
            if hold is not None and not version_satisfies_ceiling(
                landed, hold.max_version
            ):
                restore_workspace_snapshot(ws.path, snapshot, "deno")
                still_behind.append(change)
                continue
            landed_tuple = _parseable_semver_tuple(landed)
            from_tuple = _parseable_semver_tuple(change.from_ver)
            moved_up = (
                landed != change.from_ver
                if landed_tuple is None or from_tuple is None
                else landed_tuple > from_tuple
            )
            if moved_up:
                resolved_change = Change(change.name, change.from_ver, landed)
                try:
                    assert_targets_resolved(
                        ws,
                        [change],
                        manager="deno",
                        fallback_resolver=_landed_deno_version,
                    )
                except RuntimeError:
                    restore_workspace_snapshot(ws.path, snapshot, "deno")
                    still_behind.append(change)
                else:
                    applied.append(resolved_change)
                continue
            restore_workspace_snapshot(ws.path, snapshot, "deno")
            still_behind.append(change)
        else:
            restore_workspace_snapshot(ws.path, snapshot, "deno")
            still_behind.append(change)
    return applied, still_behind


def _enforce_deno_version_ceilings(
    ws: Workspace, candidate_names: list[str] | None = None
) -> None:
    """Clamp held packages that remain above their ceiling after an update."""
    candidates = set(candidate_names or ())
    candidates.update(_deno_lock_package_names(ws))
    for name in sorted(candidates):
        rule = _resolve_deno_hold(ws, name)
        if rule is None:
            continue
        resolved = _landed_deno_version(ws, name)
        if resolved is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        snapshot = snapshot_workspace_files(ws.path, "deno")
        proc = run(
            ["deno", "update", name],
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        if proc.returncode != 0:
            restore_workspace_snapshot(ws.path, snapshot, "deno")
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"(deno): {proc.stderr.strip()}"
            )
        landed = _landed_deno_version(ws, name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            restore_workspace_snapshot(ws.path, snapshot, "deno")
            raise RuntimeError(
                f"hold: {name} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} (deno)"
            )


def apply_deno(ws: Workspace) -> list[Change]:
    """Run ``deno outdated --update`` to upgrade dependencies.

    Requires Deno ≥ 2.x; ``deno outdated --update`` rewrites ``deno.lock``
    and import maps in-place. When ``REPO_OPS_DENO_UPDATE_LATEST`` is unset,
    Deno's default update stays range-respecting (Update column); when set,
    ``--latest`` is passed so cross-range bumps are applied.

    After a successful update, planned targets are verified via
    :func:`assert_targets_resolved` (ghost-bump guard parity with Node PMs).
    Returns ``behind_latest`` gaps that remain after the update.
    """
    if not shutil.which("deno"):
        raise RuntimeError(f"deno not on PATH for {ws.path}")
    planned, behind = check_deno(ws)
    cmd = ["deno", "outdated", "--update"]
    if _deno_update_latest_enabled():
        cmd.append("--latest")
    proc = run(
        cmd,
        ws.path,
        timeout=resolve_timeout_apply(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "deno outdated --update failed")
    guardable = [
        ch
        for ch in planned
        if (ch.to or "").strip() not in ("", "?", "latest") and (ch.name or "").strip()
    ]
    if guardable:
        assert_targets_resolved(
            ws,
            guardable,
            manager="deno",
            fallback_resolver=_landed_deno_version,
        )
    _, behind = targeted_bump_behind(ws, behind)
    _enforce_deno_version_ceilings(ws, [change.name for change in [*planned, *behind]])
    return behind
