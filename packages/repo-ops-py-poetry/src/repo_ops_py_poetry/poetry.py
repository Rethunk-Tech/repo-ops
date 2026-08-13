"""poetry upgrade check/apply."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import re
import shutil
import sys
from functools import cache
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import quote

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.version_utils import hold_ceiling_spec, version_satisfies_ceiling
from repo_ops_models import Change, Workspace

_POETRY_TARGETED_BUMP_ENV = "REPO_OPS_POETRY_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")

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
    raw = resolve_setting(
        "poetry_targeted_bump",
        cli_value=process_env_cli_override(_POETRY_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUE_VALUES


def _pypi_latest(package: str) -> str | None:
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
    releases = document.get("releases")
    if not isinstance(releases, dict):
        return None
    candidates = [
        version
        for version, files in releases.items()
        if isinstance(version, str)
        and (
            not isinstance(files, list)
            or any(
                isinstance(file, dict) and file.get("yanked") is not True
                for file in files
            )
        )
    ]
    return max(candidates, key=_poetry_version_parts, default=None)


def _poetry_version_parts(version: str) -> tuple[int, int, int]:
    match = re.search(r"\d+(?:\.\d+){0,2}", version.strip().lstrip("vV"))
    if match is None:
        return (0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


def _poetry_caret_upper_bound(
    parts: tuple[int, int, int],
) -> tuple[int, int, int]:
    major, minor, patch = parts
    if major:
        return (major + 1, 0, 0)
    if minor:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _poetry_requirement_satisfied(version: str, requirement: str) -> bool:
    candidate = _poetry_version_parts(version)
    for alternative in requirement.split("||"):
        constraints = [part.strip() for part in alternative.split(",") if part.strip()]
        if not constraints:
            return True
        satisfied = True
        for constraint in constraints:
            match = re.match(
                r"^(~=|<=|>=|==|<|>|=|~|\^)?\s*"
                r"(\d+(?:\.\d+){0,2}|\*)(?:\s*;.*)?$",
                constraint,
            )
            if match is None:
                satisfied = False
                break
            operator = match.group(1) or "="
            raw_version = match.group(2)
            if raw_version == "*":
                continue
            parts = _poetry_version_parts(raw_version)
            if operator == "^":
                if not (
                    candidate >= parts and candidate < _poetry_caret_upper_bound(parts)
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
        changes.append(Change(name, requirement, latest))
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


def apply_poetry(ws: Workspace) -> None:
    if not shutil.which("poetry"):
        raise RuntimeError(f"poetry not on PATH for {ws.path}")
    proc = run(["poetry", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")
    _enforce_version_ceilings(ws)
