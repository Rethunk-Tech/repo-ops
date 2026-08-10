"""Deno upgrade check/apply."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
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


def _deno_update_latest_enabled() -> bool:
    return os.environ.get(_DENO_UPDATE_LATEST_ENV, "").strip().lower() in _TRUE_VALUES


def _deno_targeted_bump_enabled() -> bool:
    return os.environ.get(_DENO_TARGETED_BUMP_ENV, "").strip().lower() in _TRUE_VALUES


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
    return _parse_deno_outdated(proc.stdout)


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
    for resolved in sorted(versions_deno_lock(Path(ws.path) / "deno.lock")):
        if resolved.startswith(prefix):
            version = resolved[len(prefix) :].strip()
            if version:
                return version
    return None


def targeted_bump_behind(
    ws: Workspace, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Run a reversible per-package ``deno update`` for behind-latest deps."""
    if not behind or not _deno_targeted_bump_enabled():
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
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
            applied.append(
                Change(change.name, change.from_ver, landed) if landed else change
            )
        else:
            restore_workspace_snapshot(ws.path, snapshot, "deno")
            still_behind.append(change)
    return applied, still_behind


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
        # Deno may not populate node_modules; assert_targets_resolved skips when
        # the resolver returns None, so this is best-effort for npm: specifiers.
        assert_targets_resolved(ws, guardable, manager="deno")
    _, behind = targeted_bump_behind(ws, behind)
    return behind
