"""pipenv upgrade check/apply."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import (
    read_json_file,
    read_toml_file,
    restore_file,
    snapshot_file,
)
from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_core.process import run
from repo_ops_core.requirement import requirement_satisfied
from repo_ops_core.version_utils import version_parts
from repo_ops_models import Change, Workspace
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.pypi import pypi_latest as _pypi_latest
from repo_ops_py.version_ceiling import enforce_version_ceilings

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# pipenv has no per-package targeted install — ``pipenv update`` regenerates
# the whole lockfile — so this clamp-down is the only enforcement mechanism.


def _pipfile_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from Pipfile.lock's default/develop sections."""
    data = read_json_file(ws.path / "Pipfile.lock") or {}
    out: dict[str, str] = {}
    for section in ("default", "develop"):
        for name, meta in (data.get(section) or {}).items():
            if not isinstance(meta, dict):
                continue
            ver = str(meta.get("version") or "").lstrip("=")
            if name and ver:
                out[str(name)] = ver
    return out


def _pipenv_requirement_satisfied(version: str, requirement: str) -> bool:
    return requirement_satisfied(version, requirement, default_operator="==")


def _pipenv_direct_dependencies(manifest: Path) -> dict[str, str]:
    document = read_toml_file(manifest) or {}
    dependencies: dict[str, str] = {}
    for section in ("packages", "dev-packages"):
        table = document.get(section)
        if not isinstance(table, dict):
            continue
        for name, value in table.items():
            requirement: object = value
            if isinstance(value, dict):
                if any(key in value for key in ("git", "path", "file", "editable", "ref", "index")):
                    continue
                requirement = value.get("version")
            if isinstance(name, str) and isinstance(requirement, str):
                dependencies.setdefault(name, requirement)
    return dependencies


def _pipenv_latest_direct_changes(ws: Workspace) -> list[Change]:
    lock_versions = _pipfile_lock_versions(ws)
    changes: list[Change] = []
    for name, requirement in sorted(_pipenv_direct_dependencies(ws.path / "Pipfile").items()):
        locked = lock_versions.get(name)
        latest = _pypi_latest(name)
        if (
            locked is None
            or latest is None
            or version_parts(latest) <= version_parts(locked)
            or _pipenv_requirement_satisfied(latest, requirement)
        ):
            continue
        changes.append(Change(name, locked, latest))
    return changes


def _enforce_version_ceilings(ws: Workspace) -> None:
    enforce_version_ceilings(
        ws,
        tool="pipenv",
        manifest_name="Pipfile",
        lock_versions=_pipfile_lock_versions,
        clamp_command=lambda name, spec: ["pipenv", "install", spec],
        run=run,
    )


def _pipenv_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    for operator in ("^", "~=", "~", "==", ">=", "<=", "!=", ">", "<", "="):
        if stripped.startswith(operator):
            return f"{operator}{latest}"
    return latest


def _apply_pipenv_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    venv = run(["pipenv", "--venv"], ws.path, timeout=TIMEOUT_APPLY)
    if venv.returncode != 0:
        return candidates
    manifest = ws.path / "Pipfile"
    lockfile = ws.path / "Pipfile.lock"
    remaining: list[Change] = []
    for candidate in candidates:
        rule = resolve_package_hold(ws, candidate.name)
        if not target_within_hold(candidate.to, rule):
            remaining.append(candidate)
            continue
        manifest_snapshot = snapshot_file(manifest)
        lock_snapshot = snapshot_file(lockfile)
        requirement = _pipenv_direct_dependencies(manifest).get(candidate.name)
        if requirement is None:
            remaining.append(candidate)
            continue
        bumped = _pipenv_bumped_requirement(requirement, candidate.to)
        try:
            proc = run(
                ["pipenv", "install", f"{candidate.name}@{bumped}"],
                ws.path,
                timeout=TIMEOUT_APPLY,
            )
        except Exception:
            restore_file(manifest, manifest_snapshot)
            restore_file(lockfile, lock_snapshot)
            raise
        landed = _pipfile_lock_versions(ws).get(candidate.name)
        if proc.returncode == 0 and landed == candidate.to:
            continue
        restore_file(manifest, manifest_snapshot)
        restore_file(lockfile, lock_snapshot)
        remaining.append(candidate)
    return remaining


def check_pipenv(
    ws: Workspace,
) -> list[Change] | tuple[list[Change], list[Change]]:
    """Return pending upgrades for a pipenv-managed workspace.

    Uses the venv's own pip directly (``pip list --outdated --format=json``)
    to enumerate packages whose installed version lags the latest on PyPI.
    Never creates the virtualenv — probes for an existing venv via
    ``pipenv --venv`` first; returns [] with a WARN when none is found so
    that plan is always read-only.

    Falls back to an empty list with a WARN when ``pipenv`` is absent.
    """
    if not shutil.which("pipenv"):
        print(f"WARN {ws.path}: pipenv not on PATH", file=sys.stderr)
        return ([], [])
    # Read-only probe: get venv path without creating one.
    probe = run(["pipenv", "--venv"], ws.path, timeout=TIMEOUT_CHECK)
    if probe.returncode != 0:
        print(
            f"WARN {ws.path}: pipenv venv not initialised; run 'pipenv install' first",
            file=sys.stderr,
        )
        return [], _pipenv_latest_direct_changes(ws)
    venv_path = probe.stdout.strip()
    # Derive pip from the venv path; fall back to bare "pip" when the probe
    # returned an unusable path (e.g. in unit tests with a mocked run).
    if venv_path and Path(venv_path).is_absolute():
        pip_cmd = str(Path(venv_path) / "bin" / "pip")
    else:
        pip_cmd = "pip"
    proc = run(
        [pip_cmd, "list", "--outdated", "--format=json"],
        ws.path,
        timeout=TIMEOUT_CHECK,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip list --outdated failed")
    changes: list[Change] = []
    try:
        pkgs = json.loads(proc.stdout)
        for entry in pkgs:
            changes.append(Change(entry["name"], entry["version"], entry["latest_version"]))
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fallback: column format (older pip versions without JSON support)
        for line in proc.stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 3:
                changes.append(Change(parts[0], parts[1], parts[2]))
    in_range = _filter_held_changes(ws, changes)
    return in_range, _pipenv_latest_direct_changes(ws)


def apply_pipenv(ws: Workspace) -> list[Change] | None:
    """Run ``pipenv update`` to upgrade all packages and regenerate Pipfile.lock."""
    if not shutil.which("pipenv"):
        raise RuntimeError(f"pipenv not on PATH for {ws.path}")
    proc = run(["pipenv", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pipenv update failed")
    _enforce_version_ceilings(ws)
    return _apply_pipenv_targeted_bumps(ws, _pipenv_latest_direct_changes(ws))
