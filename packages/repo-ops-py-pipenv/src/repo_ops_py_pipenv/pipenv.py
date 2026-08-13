"""pipenv upgrade check/apply."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import re
import shutil
import sys
from functools import cache
from pathlib import Path

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_json_file, read_toml_file
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.version_utils import hold_ceiling_spec, version_satisfies_ceiling
from repo_ops_models import Change, Workspace

_PIPENV_TARGETED_BUMP_ENV = "REPO_OPS_PIPENV_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# pipenv has no per-package targeted install — ``pipenv update`` regenerates
# the whole lockfile — so this clamp-down is the only enforcement mechanism.


@contextmanager
def _preserve_manifest(path: Path) -> Iterator[None]:
    original = path.read_bytes() if path.exists() else None
    try:
        yield
    finally:
        if original is not None:
            path.write_bytes(original)


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


def _pipenv_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "pipenv_targeted_bump",
        cli_value=process_env_cli_override(_PIPENV_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUE_VALUES


def _pipenv_version_parts(version: str) -> tuple[int, int, int]:
    match = re.search(r"\d+(?:\.\d+){0,2}", version.strip().lstrip("vV"))
    if match is None:
        return (0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


def _pipenv_caret_upper_bound(
    parts: tuple[int, int, int],
) -> tuple[int, int, int]:
    major, minor, patch = parts
    if major:
        return (major + 1, 0, 0)
    if minor:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _pipenv_requirement_satisfied(version: str, requirement: str) -> bool:
    candidate = _pipenv_version_parts(version)
    for alternative in requirement.split("||"):
        constraints = [part.strip() for part in alternative.split(",") if part.strip()]
        if not constraints:
            return True
        satisfied = True
        for constraint in constraints:
            match = re.match(
                r"^(~=|<=|>=|==|!=|<|>|=|~|\^)?\s*"
                r"(\d+(?:\.\d+){0,2}|\*)(?:\s*;.*)?$",
                constraint,
            )
            if match is None:
                satisfied = False
                break
            operator = match.group(1) or "=="
            raw_version = match.group(2)
            if raw_version == "*":
                continue
            parts = _pipenv_version_parts(raw_version)
            if operator == "^":
                if not (
                    candidate >= parts and candidate < _pipenv_caret_upper_bound(parts)
                ):
                    satisfied = False
                    break
            elif operator in ("~", "~="):
                if not (candidate >= parts and candidate < (parts[0], parts[1] + 1, 0)):
                    satisfied = False
                    break
            elif operator in ("=", "=="):
                if candidate != parts:
                    satisfied = False
                    break
            elif operator == "!=":
                if candidate == parts:
                    satisfied = False
                    break
            elif (
                (operator == ">=" and candidate < parts)
                or (operator == ">" and candidate <= parts)
                or (operator == "<=" and candidate > parts)
                or (operator == "<" and candidate >= parts)
            ):
                satisfied = False
                break
        if satisfied:
            return True
    return False


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
                if any(
                    key in value
                    for key in ("git", "path", "file", "editable", "ref", "index")
                ):
                    continue
                requirement = value.get("version")
            if isinstance(name, str) and isinstance(requirement, str):
                dependencies.setdefault(name, requirement)
    return dependencies


def _pypi_latest(package: str) -> str | None:
    from http.client import HTTPSConnection
    from urllib.parse import quote

    connection = HTTPSConnection("pypi.org", timeout=resolve_timeout_check())
    try:
        connection.request(
            "GET",
            f"/pypi/{quote(package, safe='')}/json",
            headers={"User-Agent": "repo-ops"},
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            return None
        document = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        connection.close()
    if not isinstance(document, dict):
        return None
    info = document.get("info")
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        return info["version"]
    return None


def _pipenv_latest_direct_changes(ws: Workspace) -> list[Change]:
    lock_versions = _pipfile_lock_versions(ws)
    changes: list[Change] = []
    for name, requirement in sorted(
        _pipenv_direct_dependencies(ws.path / "Pipfile").items()
    ):
        locked = lock_versions.get(name)
        latest = _pypi_latest(name)
        if (
            locked is None
            or latest is None
            or _pipenv_version_parts(latest) <= _pipenv_version_parts(locked)
            or _pipenv_requirement_satisfied(latest, requirement)
        ):
            continue
        changes.append(Change(name, locked, latest))
    return changes


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
        spec = f"{name}{hold_ceiling_spec(rule.max_version)}"
        with _preserve_manifest(ws.path / "Pipfile"):
            proc = run(
                ["pipenv", "install", spec],
                ws.path,
                timeout=resolve_timeout_apply(),
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


def _pipenv_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    for operator in ("^", "~=", "~", "==", ">=", "<=", "!=", ">", "<", "="):
        if stripped.startswith(operator):
            return f"{operator}{latest}"
    return latest


def _pipenv_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _pipenv_restore(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)


def _apply_pipenv_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    venv = run(["pipenv", "--venv"], ws.path, timeout=resolve_timeout_apply())
    if venv.returncode != 0:
        return candidates
    manifest = ws.path / "Pipfile"
    lockfile = ws.path / "Pipfile.lock"
    remaining: list[Change] = []
    for candidate in candidates:
        rule = _resolve_pkg_hold(ws, candidate.name)
        if rule is not None and not version_satisfies_ceiling(
            candidate.to, rule.max_version
        ):
            remaining.append(candidate)
            continue
        manifest_snapshot = _pipenv_snapshot(manifest)
        lock_snapshot = _pipenv_snapshot(lockfile)
        requirement = _pipenv_direct_dependencies(manifest).get(
            candidate.name, candidate.from_ver
        )
        bumped = _pipenv_bumped_requirement(requirement, candidate.to)
        try:
            proc = run(
                ["pipenv", "install", f"{candidate.name}@{bumped}"],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
        except Exception:
            _pipenv_restore(manifest, manifest_snapshot)
            _pipenv_restore(lockfile, lock_snapshot)
            raise
        landed = _pipfile_lock_versions(ws).get(candidate.name)
        if proc.returncode == 0 and landed == candidate.to:
            continue
        _pipenv_restore(manifest, manifest_snapshot)
        _pipenv_restore(lockfile, lock_snapshot)
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
        return ([], []) if _pipenv_targeted_bump_enabled(ws) else []
    # Read-only probe: get venv path without creating one.
    probe = run(["pipenv", "--venv"], ws.path, timeout=resolve_timeout_check())
    if probe.returncode != 0:
        print(
            f"WARN {ws.path}: pipenv venv not initialised; run 'pipenv install' first",
            file=sys.stderr,
        )
        if _pipenv_targeted_bump_enabled(ws):
            return [], _pipenv_latest_direct_changes(ws)
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
    in_range = _filter_held_changes(ws, changes)
    if not _pipenv_targeted_bump_enabled(ws):
        return in_range
    return in_range, _pipenv_latest_direct_changes(ws)


def apply_pipenv(ws: Workspace) -> list[Change] | None:
    """Run ``pipenv update`` to upgrade all packages and regenerate Pipfile.lock."""
    if not shutil.which("pipenv"):
        raise RuntimeError(f"pipenv not on PATH for {ws.path}")
    proc = run(["pipenv", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pipenv update failed")
    _enforce_version_ceilings(ws)
    if not _pipenv_targeted_bump_enabled(ws):
        return None
    return _apply_pipenv_targeted_bumps(ws, _pipenv_latest_direct_changes(ws))
