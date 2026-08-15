"""poetry upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from repo_ops_core.config import resolve_bool_setting
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.holds import HoldRule, resolve_package_hold
from repo_ops_core.process import run
from repo_ops_core.requirement import requirement_satisfied
from repo_ops_core.version_utils import hold_ceiling_spec, version_satisfies_ceiling
from repo_ops_models import Change, Workspace
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.pypi import pypi_latest as _pypi_latest

_POETRY_TARGETED_BUMP_ENV = "REPO_OPS_POETRY_TARGETED_BUMP"

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# poetry has no per-package targeted install — ``poetry update`` regenerates
# the whole lockfile — so this clamp-down is the only enforcement mechanism.


@contextmanager
def _preserve_manifest(path: Path) -> Iterator[None]:
    original = path.read_bytes() if path.exists() else None
    try:
        yield
    finally:
        if original is not None:
            path.write_bytes(original)


def _resolve_pkg_hold(ws: Workspace, package: str) -> HoldRule | None:
    return resolve_package_hold(ws, package)


def _poetry_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from poetry.lock's ``[[package]]`` entries."""
    data = read_toml_file(ws.path / "poetry.lock") or {}
    out: dict[str, str] = {}
    for pkg in data.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version:
            out[str(name)] = str(version)
    return out


def _poetry_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    return resolve_bool_setting("poetry_targeted_bump", _POETRY_TARGETED_BUMP_ENV, ws)


def _poetry_version_parts(version: str) -> tuple[int, int, int]:
    match = re.search(r"\d+(?:\.\d+){0,2}", version.strip().lstrip("vV"))
    if match is None:
        return (0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


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
            or _poetry_version_parts(latest) <= _poetry_version_parts(locked)
            or _poetry_requirement_satisfied(latest, requirement)
        ):
            continue
        changes.append(Change(name, locked, latest))
    return changes


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Post-apply clamp-down: correct any locked package resolved above its ceiling.

    Mirrors ``repo_ops_py.uv_lock``'s enforcement — see its docstring for why
    this is the only mechanism (no per-package targeted install) and why it
    also catches a hold added after the package was already locked past it.
    """
    versions = _poetry_lock_versions(ws)
    if not versions:
        return
    for name, resolved in versions.items():
        rule = _resolve_pkg_hold(ws, name)
        if rule is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        spec = f"{name}{hold_ceiling_spec(rule.max_version)}"
        with _preserve_manifest(ws.path / "pyproject.toml"):
            proc = run(
                ["poetry", "add", spec],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"(poetry): {proc.stderr.strip()}"
            )
        landed = _poetry_lock_versions(ws).get(name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {name} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} (poetry)"
            )


def _poetry_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    if stripped.startswith("^"):
        return f"^{latest}"
    if stripped.startswith("~"):
        return f"~{latest}"
    return latest


def _poetry_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _poetry_restore(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)


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
        rule = _resolve_pkg_hold(ws, candidate.name)
        if rule is not None and not version_satisfies_ceiling(
            candidate.to, rule.max_version
        ):
            remaining.append(candidate)
            continue
        manifest_snapshot = _poetry_snapshot(manifest)
        lock_snapshot = _poetry_snapshot(lockfile)
        bumped = _poetry_bumped_requirement(requirement, candidate.to)
        try:
            proc = run(
                ["poetry", "add", f"{candidate.name}@{bumped}"],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
        except Exception:
            _poetry_restore(manifest, manifest_snapshot)
            _poetry_restore(lockfile, lock_snapshot)
            raise
        landed = _poetry_lock_versions(ws).get(candidate.name)
        if proc.returncode == 0 and landed == candidate.to:
            continue
        _poetry_restore(manifest, manifest_snapshot)
        _poetry_restore(lockfile, lock_snapshot)
        remaining.append(candidate)
    return remaining


def check_poetry(
    ws: Workspace,
) -> list[Change] | tuple[list[Change], list[Change]]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return ([], []) if _poetry_targeted_bump_enabled(ws) else []
    proc = run(
        ["poetry", "update", "--dry-run"], ws.path, timeout=resolve_timeout_check()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        m = re.search(
            r"(?:Updating|Upgrading)\s+(\S+)\s+\(([^)]+)\s+->\s+([^)]+)\)", line
        )
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    in_range = _filter_held_changes(ws, changes)
    if not _poetry_targeted_bump_enabled(ws):
        return in_range
    return in_range, _poetry_latest_direct_changes(ws)


def apply_poetry(ws: Workspace) -> list[Change] | None:
    if not shutil.which("poetry"):
        raise RuntimeError(f"poetry not on PATH for {ws.path}")
    proc = run(["poetry", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")
    _enforce_version_ceilings(ws)
    if not _poetry_targeted_bump_enabled(ws):
        return None
    return _apply_poetry_targeted_bumps(ws, _poetry_latest_direct_changes(ws))
