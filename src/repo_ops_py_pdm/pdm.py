"""PDM upgrade check/apply."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import (
    guard_tool_writes,
    restore_file,
    snapshot_file,
)
from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_lockfiles.parsers import toml_lock_versions
from repo_ops_models import Change, Workspace
from repo_ops_py.direct_requirements import (
    bumped_requirement,
    direct_dependencies,
    latest_version,
    requirement_satisfied,
)
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.version_ceiling import enforce_version_ceilings

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# In-range ``pdm update`` handles the lockfile; opt-in targeted ``pdm add``
# moves direct constraints, while this clamp restores held manifests.


def _pdm_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from pdm.lock's ``[[package]]`` entries."""
    return toml_lock_versions(ws.path / "pdm.lock")


def _pdm_latest_direct_changes(ws: Workspace) -> list[Change]:
    dependencies = direct_dependencies(ws)
    locked = _pdm_lock_versions(ws)
    locked_normalized = {name.lower(): version for name, version in locked.items()}
    changes: list[Change] = []
    for name, requirement in sorted(dependencies.items()):
        resolved = locked_normalized.get(name.lower())
        if resolved is None:
            continue
        try:
            latest = latest_version(name)
        except RuntimeError:
            continue
        if semver_tuple(latest) > semver_tuple(resolved) and not requirement_satisfied(
            latest, requirement
        ):
            changes.append(Change(name, resolved, latest))
    return changes


def _apply_pdm_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    manifest = ws.path / "pyproject.toml"
    lockfile = ws.path / "pdm.lock"
    dependencies = direct_dependencies(ws)
    remaining: list[Change] = []
    for candidate in candidates:
        requirement = dependencies.get(candidate.name)
        if requirement is None:
            remaining.append(candidate)
            continue
        rule = resolve_package_hold(ws, candidate.name)
        if not target_within_hold(candidate.to, rule):
            remaining.append(candidate)
            continue
        manifest_snapshot = snapshot_file(manifest, ws.git_repo)
        lock_snapshot = snapshot_file(lockfile, ws.git_repo)
        try:
            bumped = bumped_requirement(requirement, candidate.to)
            proc = run(
                [
                    "pdm",
                    "add",
                    f"{candidate.name}@{bumped}"
                    if bumped.startswith(("^", "~")) and not bumped.startswith("~=")
                    else f"{candidate.name}{bumped}",
                ],
                ws.path,
                timeout=TIMEOUT_APPLY,
            )
        except Exception:
            restore_file(manifest, manifest_snapshot, ws.git_repo)
            restore_file(lockfile, lock_snapshot, ws.git_repo)
            raise
        landed_versions = _pdm_lock_versions(ws)
        landed = next(
            (
                version
                for name, version in landed_versions.items()
                if name.lower() == candidate.name.lower()
            ),
            None,
        )
        if proc.returncode == 0 and landed == candidate.to:
            continue
        restore_file(manifest, manifest_snapshot, ws.git_repo)
        restore_file(lockfile, lock_snapshot, ws.git_repo)
        remaining.append(candidate)
    return remaining


def _enforce_version_ceilings(ws: Workspace) -> None:
    enforce_version_ceilings(
        ws,
        tool="pdm",
        manifest_name="pyproject.toml",
        lock_versions=_pdm_lock_versions,
        clamp_command=lambda name, spec: ["pdm", "add", spec],
        run=run,
    )


def check_pdm(
    ws: Workspace,
) -> list[Change] | tuple[list[Change], list[Change]]:
    """Return pending upgrades for a PDM-managed workspace.

    Re-locks a scratch copy with ``pdm update --no-sync`` and reports the
    packages whose locked version moved.

    Falls back to an empty list with a WARN when ``pdm`` is absent so the
    workspace degrades gracefully (report-only, no error).
    """
    if not shutil.which("pdm"):
        print(f"WARN {ws.path}: pdm not on PATH", file=sys.stderr)
        return ([], [])
    # `pdm update --dry-run` prints no "old -> new" without an installed venv and
    # creates .venv/.pdm-python in the checkout, so the plan re-locks a scratch
    # copy and diffs the lockfile.
    before = _pdm_lock_versions(ws)
    with tempfile.TemporaryDirectory(prefix="repo-ops-pdm-") as td:
        scratch = Path(td)
        shutil.copytree(
            ws.path,
            scratch,
            symlinks=True,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "node_modules"),
            ignore_dangling_symlinks=True,
        )
        proc = run(["pdm", "update", "--no-sync"], scratch, timeout=TIMEOUT_CHECK)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "pdm update --no-sync failed")
        after = toml_lock_versions(scratch / "pdm.lock")
    changes = [
        Change(name, old, after[name])
        for name, old in sorted(before.items())
        if name in after and after[name] != old
    ]
    in_range = _filter_held_changes(ws, changes)
    return in_range, _pdm_latest_direct_changes(ws)


def apply_pdm(ws: Workspace) -> list[Change] | None:
    """Run ``pdm update`` to upgrade all dependencies in the lockfile."""
    if not shutil.which("pdm"):
        raise RuntimeError(f"pdm not on PATH for {ws.path}")
    # pdm writes through a symlinked manifest/lockfile; the snapshot skips
    # links, so a rollback could not restore an outside file.
    if not guard_tool_writes(ws.git_repo, "pdm", ws.path / "pdm.lock", ws.path / "pyproject.toml"):
        return None
    proc = run(["pdm", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pdm update failed")
    _enforce_version_ceilings(ws)
    return _apply_pdm_targeted_bumps(ws, _pdm_latest_direct_changes(ws))
