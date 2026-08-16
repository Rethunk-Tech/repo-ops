"""PDM upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops_core.config import resolve_bool_setting
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_toml_file, restore_file, snapshot_file
from repo_ops_core.holds import resolve_package_hold
from repo_ops_core.process import run
from repo_ops_core.requirement import requirement_satisfied
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_models import Change, Workspace
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.pypi import pypi_latest as _pypi_latest
from repo_ops_py.version_ceiling import enforce_version_ceilings

_PDM_TARGETED_BUMP_ENV = "REPO_OPS_PDM_TARGETED_BUMP"


def _pdm_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    return resolve_bool_setting("pdm_targeted_bump", _PDM_TARGETED_BUMP_ENV, ws)


# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# In-range ``pdm update`` handles the lockfile; opt-in targeted ``pdm add``
# moves direct constraints, while this clamp restores held manifests.


def _pdm_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from pdm.lock's ``[[package]]`` entries."""
    data = read_toml_file(ws.path / "pdm.lock") or {}
    out: dict[str, str] = {}
    for pkg in data.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version:
            out[str(name)] = str(version)
    return out


def _pdm_direct_dependencies(ws: Workspace) -> dict[str, str]:
    document = read_toml_file(ws.path / "pyproject.toml") or {}
    dependencies: dict[str, str] = {}
    project = document.get("project")
    if isinstance(project, dict):
        raw_dependencies = project.get("dependencies")
        if isinstance(raw_dependencies, list):
            for raw_requirement in raw_dependencies:
                if not isinstance(raw_requirement, str):
                    continue
                match = re.match(
                    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(.*)$",
                    raw_requirement,
                )
                if match:
                    dependencies[match.group(1)] = match.group(2).strip()
    tool = document.get("tool")
    pdm = tool.get("pdm") if isinstance(tool, dict) else None
    pdm_dependencies = pdm.get("dependencies") if isinstance(pdm, dict) else None
    if isinstance(pdm_dependencies, dict):
        for name, requirement in pdm_dependencies.items():
            if isinstance(name, str) and isinstance(requirement, str):
                dependencies[name] = requirement.strip()
    return dependencies


def _pdm_latest_version(package: str) -> str:
    latest = _pypi_latest(package)
    if latest is None:
        raise RuntimeError(f"registry lookup failed for {package}")
    return latest


def _pdm_requirement_satisfied(version: str, requirement: str) -> bool:
    return requirement_satisfied(version, requirement, default_operator="^")


def _pdm_latest_direct_changes(ws: Workspace) -> list[Change]:
    dependencies = _pdm_direct_dependencies(ws)
    locked = _pdm_lock_versions(ws)
    locked_normalized = {name.lower(): version for name, version in locked.items()}
    changes: list[Change] = []
    for name, requirement in sorted(dependencies.items()):
        resolved = locked_normalized.get(name.lower())
        if resolved is None:
            continue
        try:
            latest = _pdm_latest_version(name)
        except RuntimeError:
            continue
        if semver_tuple(latest) > semver_tuple(resolved) and not _pdm_requirement_satisfied(
            latest, requirement
        ):
            changes.append(Change(name, resolved, latest))
    return changes


def _pdm_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    if stripped.startswith("^"):
        return f"^{latest}"
    if stripped.startswith("~"):
        return f"~{latest}"
    return f"=={latest}"


def _apply_pdm_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    manifest = ws.path / "pyproject.toml"
    lockfile = ws.path / "pdm.lock"
    dependencies = _pdm_direct_dependencies(ws)
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
        try:
            bumped = _pdm_bumped_requirement(requirement, candidate.to)
            proc = run(
                [
                    "pdm",
                    "add",
                    f"{candidate.name}@{bumped}"
                    if bumped.startswith(("^", "~"))
                    else f"{candidate.name}{bumped}",
                ],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
        except Exception:
            restore_file(manifest, manifest_snapshot)
            restore_file(lockfile, lock_snapshot)
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
        restore_file(manifest, manifest_snapshot)
        restore_file(lockfile, lock_snapshot)
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

    Runs ``pdm update --dry-run`` and parses lines of the form::

        - name 1.2.3 -> 1.3.0

    Falls back to an empty list with a WARN when ``pdm`` is absent so the
    workspace degrades gracefully (report-only, no error).
    """
    if not shutil.which("pdm"):
        print(f"WARN {ws.path}: pdm not on PATH", file=sys.stderr)
        return ([], []) if _pdm_targeted_bump_enabled(ws) else []
    proc = run(
        ["pdm", "update", "--dry-run"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pdm update --dry-run failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        # PDM dry-run lines look like:
        #   - requests 2.28.0 -> 2.31.0
        #   ~ requests 2.28.0 -> 2.31.0
        m = re.search(r"[-~]\s+(\S+)\s+([\d][^\s]*)\s+->\s+([\d][^\s]*)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    in_range = _filter_held_changes(ws, changes)
    if not _pdm_targeted_bump_enabled(ws):
        return in_range
    return in_range, _pdm_latest_direct_changes(ws)


def apply_pdm(ws: Workspace) -> list[Change] | None:
    """Run ``pdm update`` to upgrade all dependencies in the lockfile."""
    if not shutil.which("pdm"):
        raise RuntimeError(f"pdm not on PATH for {ws.path}")
    proc = run(["pdm", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pdm update failed")
    _enforce_version_ceilings(ws)
    if not _pdm_targeted_bump_enabled(ws):
        return None
    return _apply_pdm_targeted_bumps(ws, _pdm_latest_direct_changes(ws))
