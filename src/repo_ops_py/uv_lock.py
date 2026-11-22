"""Shared uv.lock check/apply helpers (used by py-uv and py-hatch)."""

from __future__ import annotations

import re

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import guard_tool_writes, restore_file, snapshot_file
from repo_ops_core.holds import promote_unheld, resolve_package_hold, target_within_hold
from repo_ops_core.process import run
from repo_ops_core.version_utils import pep440_precedence, semver_tuple
from repo_ops_lockfiles.parsers import toml_lock_versions
from repo_ops_models import Change, Workspace
from repo_ops_py.direct_requirements import (
    bumped_requirement,
    direct_dependencies,
    latest_version,
    requirement_satisfied,
)
from repo_ops_py.holds import ceiling_target
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.version_ceiling import enforce_version_ceilings

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# uv (and hatch, which delegates here) has no per-package targeted install —
# ``uv lock --upgrade`` regenerates the whole lockfile — so unlike the JS
# ecosystems, this clamp-down is the *only* enforcement mechanism, not a
# safety net.


def _uv_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from uv.lock's ``[[package]]`` entries."""
    return toml_lock_versions(ws.path / "uv.lock")


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Post-apply lock-only clamp: ``uv lock --upgrade-package <pkg><=ceiling``.

    Also catches a hold added after the package was already locked above the
    ceiling: ``uv lock --upgrade --dry-run`` only reports packages that would
    move, so a package already past a brand-new ceiling never appears in
    ``check_uv_lock``'s changes.
    """
    enforce_version_ceilings(
        ws,
        tool="uv",
        manifest_name="pyproject.toml",
        lock_versions=_uv_lock_versions,
        clamp_command=lambda name, spec: ["uv", "lock", "--upgrade-package", spec],
        run=run,
    )


def _uv_latest_direct_changes(ws: Workspace) -> list[Change]:
    """Direct dependencies whose manifest range excludes the newest release."""
    locked = {name.lower(): version for name, version in _uv_lock_versions(ws).items()}
    changes: list[Change] = []
    for name, requirement in sorted(direct_dependencies(ws).items()):
        resolved = locked.get(name.lower())
        if resolved is None:
            continue
        try:
            latest = latest_version(name)
        except RuntimeError:
            continue
        latest = ceiling_target(ws, name, latest)
        if semver_tuple(latest) > semver_tuple(resolved) and not requirement_satisfied(
            latest, requirement
        ):
            changes.append(Change(name, resolved, latest))
    return changes


def _apply_uv_targeted_bumps(ws: Workspace, candidates: list[Change]) -> list[Change]:
    """Rewrite each blocking range with ``uv add``; restore on failure or no advance."""
    manifest = ws.path / "pyproject.toml"
    lockfile = ws.path / "uv.lock"
    dependencies = direct_dependencies(ws)
    remaining: list[Change] = []
    for candidate in candidates:
        requirement = dependencies.get(candidate.name)
        rule = resolve_package_hold(ws, candidate.name)
        if requirement is None or not target_within_hold(candidate.to, rule):
            remaining.append(candidate)
            continue
        manifest_snapshot = snapshot_file(manifest, ws.git_repo)
        lock_snapshot = snapshot_file(lockfile, ws.git_repo)
        try:
            proc = run(
                [
                    "uv",
                    "add",
                    f"{candidate.name}{bumped_requirement(requirement, candidate.to, rule)}",
                ],
                ws.path,
                timeout=TIMEOUT_APPLY,
            )
        except Exception:
            restore_file(manifest, manifest_snapshot, ws.git_repo)
            restore_file(lockfile, lock_snapshot, ws.git_repo)
            raise
        landed = {n.lower(): v for n, v in _uv_lock_versions(ws).items()}.get(
            candidate.name.lower()
        )
        if proc.returncode == 0 and landed == candidate.to:
            continue
        restore_file(manifest, manifest_snapshot, ws.git_repo)
        restore_file(lockfile, lock_snapshot, ws.git_repo)
        remaining.append(candidate)
    return remaining


def check_uv_lock(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Run ``uv lock --upgrade --dry-run``; return in-range changes and range-blocked latest ones."""
    proc = run(
        ["uv", "lock", "--upgrade", "--dry-run"],
        ws.path,
        timeout=TIMEOUT_CHECK,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if "would upgrade" in line.lower() or "->" in line:
            # Allow pre-release / local identifiers (e.g. 1.2.3a1, 1.0.0+local)
            # and uv's "v" version prefix.
            m = re.search(
                r"(\S+)\s+v?([0-9][^\s]*)\s*->\s*v?([0-9][^\s]*)",
                line,
            )
            if m:
                changes.append(Change(m.group(1), m.group(2), m.group(3)))
    if not changes and "would change" in (proc.stdout + proc.stderr).lower():
        changes.append(Change("(lockfile)", "current", "upgraded"))
    return promote_unheld(ws, _filter_held_changes(ws, changes), _uv_latest_direct_changes(ws))


def _raise_on_downgrade(before: dict[str, str], after: dict[str, str]) -> None:
    """Fail when a package still locked landed below its pre-apply version.

    ``uv lock --upgrade`` resolves lower when the locked release left the index.
    """
    lowered = [
        f"{name} {old} -> {new}"
        for name, new in sorted(after.items())
        if (old := before.get(name)) and pep440_precedence(new) < pep440_precedence(old)
    ]
    if lowered:
        raise RuntimeError("uv lock --upgrade downgraded " + ", ".join(lowered))


def apply_uv_lock(ws: Workspace) -> list[Change] | None:
    """Run ``uv lock --upgrade`` and ``uv sync``, then raise ranges that block the newest release."""
    lockfile = ws.path / "uv.lock"
    manifest = ws.path / "pyproject.toml"
    # uv writes through a symlinked manifest/lockfile, and rollback cannot
    # restore an outside file.
    if not guard_tool_writes(ws.git_repo, "uv", lockfile, manifest):
        return None
    before = _uv_lock_versions(ws)
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    _raise_on_downgrade(before, _uv_lock_versions(ws))
    proc2 = run(["uv", "sync"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")
    _enforce_version_ceilings(ws)
    return _apply_uv_targeted_bumps(ws, _uv_latest_direct_changes(ws))
