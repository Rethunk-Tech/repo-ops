"""pipenv upgrade check/apply."""

from __future__ import annotations

import json
import shutil
import sys
from functools import cache
from pathlib import Path

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.version_utils import version_satisfies_ceiling
from repo_ops_models import Change, Workspace

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# pipenv has no per-package targeted install — ``pipenv update`` regenerates
# the whole lockfile — so this clamp-down is the only enforcement mechanism.

_HOLD_CEILING_PAD = "999999"


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


def _resolve_pkg_hold(ws: Workspace, package: str) -> HoldRule | None:
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


def _hold_ceiling_spec(max_version: str) -> str:
    """PEP 440 upper-bound constraint for a hold ceiling (e.g. ``<=6.999999.999999``)."""
    parts = [p for p in max_version.strip().split(".") if p]
    padded = (parts + [_HOLD_CEILING_PAD] * 3)[:3]
    return "<=" + ".".join(padded)


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


def _filter_held_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    kept: list[Change] = []
    for c in changes:
        rule = _resolve_pkg_hold(ws, c.name)
        if rule is not None and not version_satisfies_ceiling(c.to, rule.max_version):
            continue
        kept.append(c)
    return kept


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Post-apply clamp-down: correct any locked package resolved above its ceiling.

    Mirrors ``repo_ops_py.uv_lock``'s enforcement — see its docstring for why
    this is the only mechanism (no per-package targeted install) and why it
    also catches a hold added after the package was already locked past it.
    """
    versions = _pipfile_lock_versions(ws)
    if not versions:
        return
    for name, resolved in versions.items():
        rule = _resolve_pkg_hold(ws, name)
        if rule is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        spec = f"{name}{_hold_ceiling_spec(rule.max_version)}"
        proc = run(
            ["pipenv", "install", spec], ws.path, timeout=resolve_timeout_apply()
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"(pipenv): {proc.stderr.strip()}"
            )
        landed = _pipfile_lock_versions(ws).get(name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {name} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} (pipenv)"
            )


def check_pipenv(ws: Workspace) -> list[Change]:
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
        return []
    # Read-only probe: get venv path without creating one.
    probe = run(["pipenv", "--venv"], ws.path, timeout=resolve_timeout_check())
    if probe.returncode != 0:
        print(
            f"WARN {ws.path}: pipenv venv not initialised; run 'pipenv install' first",
            file=sys.stderr,
        )
        return []
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
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip list --outdated failed")
    changes: list[Change] = []
    try:
        pkgs = json.loads(proc.stdout)
        for entry in pkgs:
            changes.append(
                Change(entry["name"], entry["version"], entry["latest_version"])
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fallback: column format (older pip versions without JSON support)
        for line in proc.stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 3:
                changes.append(Change(parts[0], parts[1], parts[2]))
    return _filter_held_changes(ws, changes)


def apply_pipenv(ws: Workspace) -> None:
    """Run ``pipenv update`` to upgrade all packages and regenerate Pipfile.lock."""
    if not shutil.which("pipenv"):
        raise RuntimeError(f"pipenv not on PATH for {ws.path}")
    proc = run(["pipenv", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pipenv update failed")
    _enforce_version_ceilings(ws)
