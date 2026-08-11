"""Rust/Cargo upgrade check/apply.

Cargo has no cross-range / aggressive upgrade mode under fleet ``--latest``.
``REPO_OPS_*_UPDATE_LATEST`` and ``--latest`` do not change cargo behavior;
``cargo update`` always resolves within existing ``Cargo.toml`` / lockfile
constraints. Prefer honest docs over inventing a half-mode gate.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_models import Change, Workspace


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


def _resolve_crate_hold(ws: Workspace, crate_name: str) -> HoldRule | None:
    """Return the active hold rule for *crate_name* in this workspace."""
    repo_root = str(ws.git_repo.resolve())
    rules = [*_global_holds_cached(), *_repo_holds_cached(repo_root)]
    if not rules:
        return None
    return resolve_hold(
        rules,
        package=crate_name,
        ecosystem=ws.ecosystem,
        manager=ws.manager,
        workspace_rel=_workspace_rel(ws),
    )


def _cargo_precise_version(max_version: str) -> str:
    """Normalize a hold ceiling to an exact Cargo version target."""
    parts = [part for part in max_version.strip().lstrip("v").split(".") if part]
    padded = (parts + ["0"] * 3)[:3]
    return ".".join(padded)


def _cargo_resolved_versions(ws: Workspace) -> dict[str, tuple[str, ...]]:
    """Return resolved Cargo package versions grouped by crate name."""
    proc = run(
        ["cargo", "metadata", "--format-version", "1"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        return {}
    try:
        document = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(document, dict):
        return {}

    packages = document.get("packages")
    if not isinstance(packages, list):
        return {}
    resolved_ids: set[str] | None = None
    resolve = document.get("resolve")
    if isinstance(resolve, dict) and isinstance(resolve.get("nodes"), list):
        resolved_ids = {
            node["id"]
            for node in resolve["nodes"]
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }

    versions: dict[str, set[str]] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        version = package.get("version")
        package_id = package.get("id")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        if resolved_ids is not None and package_id not in resolved_ids:
            continue
        versions.setdefault(name, set()).add(version)
    return {name: tuple(sorted(values)) for name, values in versions.items()}


def _filter_held_rust_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    """Drop Changes whose crate target violates an active hold rule."""
    kept: list[Change] = []
    for change in changes:
        rule = _resolve_crate_hold(ws, change.name)
        if rule is not None and not version_satisfies_ceiling(
            change.to, rule.max_version
        ):
            continue
        kept.append(change)
    return kept


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Clamp resolved Cargo crates that exceed active hold ceilings."""
    resolved = _cargo_resolved_versions(ws)
    for crate_name, versions in sorted(resolved.items()):
        rule = _resolve_crate_hold(ws, crate_name)
        if rule is None:
            continue
        violating = [
            version
            for version in versions
            if not version_satisfies_ceiling(version, rule.max_version)
        ]
        if not violating:
            continue
        candidates = [
            version
            for version in versions
            if version_satisfies_ceiling(version, rule.max_version)
        ]
        target = (
            max(candidates, key=semver_tuple)
            if candidates
            else _cargo_precise_version(rule.max_version)
        )
        for resolved_version in violating:
            proc = run(
                [
                    "cargo",
                    "update",
                    "-p",
                    f"{crate_name}@{resolved_version}",
                    "--precise",
                    target,
                ],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"hold: failed to clamp {crate_name}@{resolved_version} "
                    f"back to <={rule.max_version} (cargo): {proc.stderr.strip()}"
                )
        landed = _cargo_resolved_versions(ws).get(crate_name, ())
        if not landed or any(
            not version_satisfies_ceiling(version, rule.max_version)
            for version in landed
        ):
            raise RuntimeError(
                f"hold: {crate_name} still at {', '.join(landed) or violating[-1]} "
                f"after clamp attempt, expected <= {rule.max_version} (cargo)"
            )


def check_rust(ws: Workspace) -> list[Change]:
    proc = run(
        ["cargo", "update", "--dry-run"], ws.path, timeout=resolve_timeout_check()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        m = re.search(r"Updating\s+(\S+)\s+v([\d.]+)\s+->\s+v([\d.]+)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return _filter_held_rust_changes(ws, changes)


def apply_rust(ws: Workspace) -> None:
    proc = run(["cargo", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update failed")
    _enforce_version_ceilings(ws)
