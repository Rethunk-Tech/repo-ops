"""poetry upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import read_toml_file, restore_file, snapshot_file
from repo_ops_core.holds import resolve_package_hold
from repo_ops_core.process import run
from repo_ops_core.requirement import requirement_satisfied
from repo_ops_core.version_utils import version_parts, version_satisfies_ceiling
from repo_ops_lockfiles.parsers import toml_lock_versions
from repo_ops_models import Change, Workspace
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.pypi import pypi_latest as _pypi_latest
from repo_ops_py.version_ceiling import enforce_version_ceilings

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# poetry has no per-package targeted install — ``poetry update`` regenerates
# the whole lockfile — so this clamp-down is the only enforcement mechanism.


def _poetry_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from poetry.lock's ``[[package]]`` entries."""
    return toml_lock_versions(ws.path / "poetry.lock")


def _poetry_requirement_satisfied(version: str, requirement: str) -> bool:
    return requirement_satisfied(
        version,
        requirement,
        default_operator="^",
    )


def _poetry_direct_dependencies(
    manifest: Path,
) -> dict[str, str]:
    document = read_toml_file(manifest) or {}
    tool = document.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if not isinstance(poetry, dict):
        return {}
    tables: list[object] = [
        poetry.get("dependencies"),
        poetry.get("dev-dependencies"),
    ]
    groups = poetry.get("group")
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, dict):
                tables.append(group.get("dependencies"))
    dependencies: dict[str, str] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        for name, value in table.items():
            if name == "python":
                continue
            requirement: object = value
            if isinstance(value, dict):
                if any(key in value for key in ("git", "path", "url")):
                    continue
                requirement = value.get("version")
            if isinstance(name, str) and isinstance(requirement, str):
                dependencies.setdefault(name, requirement)
    return dependencies


def _poetry_latest_direct_changes(ws: Workspace) -> list[Change]:
    lock_versions = _poetry_lock_versions(ws)
    changes: list[Change] = []
    for name, requirement in sorted(
        _poetry_direct_dependencies(ws.path / "pyproject.toml").items()
    ):
        locked = lock_versions.get(name)
        latest = _pypi_latest(name)
        if (
            locked is None
            or latest is None
            or version_parts(latest) <= version_parts(locked)
            or _poetry_requirement_satisfied(latest, requirement)
        ):
            continue
        changes.append(Change(name, locked, latest))
    return changes


def _enforce_version_ceilings(ws: Workspace) -> None:
    enforce_version_ceilings(
        ws,
        tool="poetry",
        manifest_name="pyproject.toml",
        lock_versions=_poetry_lock_versions,
        clamp_command=lambda name, spec: ["poetry", "add", spec],
        run=run,
    )


def _poetry_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    if stripped.startswith("^"):
        return f"^{latest}"
    if stripped.startswith("~"):
        return f"~{latest}"
    return latest


def _apply_poetry_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    manifest = ws.path / "pyproject.toml"
    lockfile = ws.path / "poetry.lock"
    dependencies = _poetry_direct_dependencies(manifest)
    remaining: list[Change] = []
    for candidate in candidates:
        requirement = dependencies.get(candidate.name)
        if requirement is None:
            remaining.append(candidate)
            continue
        rule = resolve_package_hold(ws, candidate.name)
        if rule is not None and not version_satisfies_ceiling(candidate.to, rule.max_version):
            remaining.append(candidate)
            continue
        manifest_snapshot = snapshot_file(manifest)
        lock_snapshot = snapshot_file(lockfile)
        bumped = _poetry_bumped_requirement(requirement, candidate.to)
        try:
            proc = run(
                ["poetry", "add", f"{candidate.name}@{bumped}"],
                ws.path,
                timeout=TIMEOUT_APPLY,
            )
        except Exception:
            restore_file(manifest, manifest_snapshot)
            restore_file(lockfile, lock_snapshot)
            raise
        landed = _poetry_lock_versions(ws).get(candidate.name)
        if proc.returncode == 0 and landed == candidate.to:
            continue
        restore_file(manifest, manifest_snapshot)
        restore_file(lockfile, lock_snapshot)
        remaining.append(candidate)
    return remaining


def check_poetry(
    ws: Workspace,
) -> list[Change] | tuple[list[Change], list[Change]]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return ([], [])
    proc = run(["poetry", "update", "--dry-run"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        m = re.search(r"(?:Updating|Upgrading)\s+(\S+)\s+\(([^)]+)\s+->\s+([^)]+)\)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    in_range = _filter_held_changes(ws, changes)
    return in_range, _poetry_latest_direct_changes(ws)


def apply_poetry(ws: Workspace) -> list[Change] | None:
    if not shutil.which("poetry"):
        raise RuntimeError(f"poetry not on PATH for {ws.path}")
    proc = run(["poetry", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")
    _enforce_version_ceilings(ws)
    return _apply_poetry_targeted_bumps(ws, _poetry_latest_direct_changes(ws))
