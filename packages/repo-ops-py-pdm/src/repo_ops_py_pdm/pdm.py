"""PDM upgrade check/apply."""

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

_PDM_TARGETED_BUMP_ENV = "REPO_OPS_PDM_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")


def _pdm_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "pdm_targeted_bump",
        cli_value=process_env_cli_override(_PDM_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUE_VALUES


# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# In-range ``pdm update`` handles the lockfile; opt-in targeted ``pdm add``
# moves direct constraints, while this clamp restores held manifests.


@contextmanager
def _preserve_manifest(path: Path) -> Iterator[None]:
    original = path.read_bytes() if path.exists() else None
    try:
        yield
    finally:
        if original is not None:
            path.write_bytes(original)
        else:
            path.unlink(missing_ok=True)


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
    connection = HTTPSConnection("pypi.org", timeout=resolve_timeout_check())
    try:
        connection.request(
            "GET",
            f"/pypi/{quote(package, safe='')}/json",
            headers={"User-Agent": "repo-ops"},
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise RuntimeError(
                f"registry lookup returned HTTP {response.status} for {package}"
            )
        document = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"registry lookup failed for {package}: {exc}") from exc
    finally:
        connection.close()
    if not isinstance(document, dict):
        raise RuntimeError(f"registry lookup returned invalid data for {package}")
    info = document.get("info")
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        return info["version"]
    releases = document.get("releases")
    if not isinstance(releases, dict):
        raise RuntimeError(f"registry lookup returned no version for {package}")
    versions = [
        version
        for version, files in releases.items()
        if isinstance(version, str)
        and isinstance(files, list)
        and any(
            isinstance(file, dict) and file.get("yanked") is not True for file in files
        )
    ]
    if not versions:
        raise RuntimeError(f"registry lookup returned no version for {package}")
    return max(versions, key=_pdm_version_key)


def _pdm_version_key(version: str) -> tuple[tuple[int, ...], str]:
    numbers = tuple(int(part) for part in re.findall(r"\d+", version))
    return numbers, version


def _pdm_caret_upper_bound(
    parts: tuple[int, ...],
) -> tuple[int, ...]:
    padded = (*parts, 0, 0, 0)
    if padded[0]:
        return (padded[0] + 1, 0, 0)
    if padded[1]:
        return (0, padded[1] + 1, 0)
    return (0, 0, padded[2] + 1)


def _pdm_requirement_satisfied(version: str, requirement: str) -> bool:
    candidate = _pdm_version_key(version)[0]
    requirement = requirement.split(";", 1)[0].strip()
    requirement = re.sub(r"^\[[^\]]+\]", "", requirement).strip()
    for alternative in requirement.split("||"):
        constraints = [part.strip() for part in alternative.split(",") if part.strip()]
        if not constraints:
            return True
        satisfied = True
        for constraint in constraints:
            match = re.match(
                r"^(~=|<=|>=|==|<|>|=|~|\^)?\s*"
                r"(\d+(?:\.\d+){0,2}|\*)$",
                constraint,
            )
            if match is None:
                satisfied = False
                break
            operator = match.group(1) or "^"
            raw_version = match.group(2)
            if raw_version == "*":
                continue
            parts = _pdm_version_key(raw_version)[0]
            padded_candidate = (*candidate, 0, 0, 0)
            padded_parts = (*parts, 0, 0, 0)
            if operator == "^":
                if not (
                    padded_candidate[:3] >= padded_parts[:3]
                    and padded_candidate[:3] < _pdm_caret_upper_bound(parts)
                ):
                    satisfied = False
                    break
            elif operator in ("~", "~="):
                if not (
                    padded_candidate[:3] >= padded_parts[:3]
                    and padded_candidate[:3] < (padded_parts[0], padded_parts[1] + 1, 0)
                ):
                    satisfied = False
                    break
            elif operator in ("=", "=="):
                if padded_candidate[:3] != padded_parts[:3]:
                    satisfied = False
                    break
            elif (
                (operator == ">=" and padded_candidate[:3] < padded_parts[:3])
                or (operator == ">" and padded_candidate[:3] <= padded_parts[:3])
                or (operator == "<=" and padded_candidate[:3] > padded_parts[:3])
                or (operator == "<" and padded_candidate[:3] >= padded_parts[:3])
            ):
                satisfied = False
                break
        if satisfied:
            return True
    return False


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
        if _pdm_version_key(latest) > _pdm_version_key(
            resolved
        ) and not _pdm_requirement_satisfied(latest, requirement):
            changes.append(Change(name, requirement, latest))
    return changes


def _pdm_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    if stripped.startswith("^"):
        return f"^{latest}"
    if stripped.startswith("~"):
        return f"~{latest}"
    return f"=={latest}"


def _pdm_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _pdm_restore(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)


def _apply_pdm_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    manifest = ws.path / "pyproject.toml"
    lockfile = ws.path / "pdm.lock"
    remaining: list[Change] = []
    for candidate in candidates:
        rule = _resolve_pkg_hold(ws, candidate.name)
        if rule is not None and not version_satisfies_ceiling(
            candidate.to, rule.max_version
        ):
            remaining.append(candidate)
            continue
        manifest_snapshot = _pdm_snapshot(manifest)
        lock_snapshot = _pdm_snapshot(lockfile)
        try:
            bumped = _pdm_bumped_requirement(candidate.from_ver, candidate.to)
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
            _pdm_restore(manifest, manifest_snapshot)
            _pdm_restore(lockfile, lock_snapshot)
            raise
        lock_advanced = _pdm_snapshot(lockfile) != lock_snapshot
        if proc.returncode == 0 and lock_advanced:
            continue
        _pdm_restore(manifest, manifest_snapshot)
        _pdm_restore(lockfile, lock_snapshot)
        remaining.append(candidate)
    return remaining


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
    versions = _pdm_lock_versions(ws)
    if not versions:
        return
    for name, resolved in versions.items():
        rule = _resolve_pkg_hold(ws, name)
        if rule is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        spec = f"{name}{hold_ceiling_spec(rule.max_version)}"
        with _preserve_manifest(ws.path / "pyproject.toml"):
            proc = run(
                ["pdm", "add", spec],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"(pdm): {proc.stderr.strip()}"
            )
        landed = _pdm_lock_versions(ws).get(name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {name} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} (pdm)"
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
