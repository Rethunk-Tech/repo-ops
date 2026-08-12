"""poetry upgrade check/apply."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import re
import shutil
import sys
from functools import cache
from pathlib import Path

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


def check_poetry(ws: Workspace) -> list[Change]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return []
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
    return _filter_held_changes(ws, changes)


def apply_poetry(ws: Workspace) -> None:
    if not shutil.which("poetry"):
        raise RuntimeError(f"poetry not on PATH for {ws.path}")
    proc = run(["poetry", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")
    _enforce_version_ceilings(ws)
