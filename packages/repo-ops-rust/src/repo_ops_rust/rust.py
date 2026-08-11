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
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import quote

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_models import Change, Workspace

_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}


def set_rust_audit_version_overrides(overrides: dict[str, str]) -> None:
    _AUDIT_VERSION_OVERRIDES.clear()
    _AUDIT_VERSION_OVERRIDES.update(overrides)


def clear_rust_audit_version_overrides() -> None:
    _AUDIT_VERSION_OVERRIDES.clear()


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


def _cargo_registry_versions(crate_name: str) -> tuple[str, ...]:
    """Return published, non-yanked versions for a crate."""
    endpoint = f"/api/v1/crates/{quote(crate_name, safe='')}/versions"
    versions: set[str] = set()
    query = "?per_page=100"
    seen_queries: set[str] = set()
    while query not in seen_queries:
        seen_queries.add(query)
        connection = HTTPSConnection(
            "crates.io",
            timeout=resolve_timeout_check(),
        )
        try:
            connection.request(
                "GET",
                f"{endpoint}{query}",
                headers={"User-Agent": "repo-ops"},
            )
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise RuntimeError(
                    f"registry lookup returned HTTP {response.status} for {crate_name}"
                )
            document = json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError(
                f"registry lookup failed for {crate_name}: {exc}"
            ) from exc
        finally:
            connection.close()
        if not isinstance(document, dict):
            raise RuntimeError(
                f"registry lookup returned invalid data for {crate_name}"
            )

        before_count = len(versions)
        entries = document.get("versions")
        if not isinstance(entries, list):
            raise RuntimeError(f"registry lookup returned no versions for {crate_name}")
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("yanked") is True:
                continue
            version = entry.get("num")
            if isinstance(version, str):
                versions.add(version)
        if len(versions) == before_count:
            break

        meta = document.get("meta")
        next_page = meta.get("next_page") if isinstance(meta, dict) else None
        if next_page in (None, "") or next_page in seen_queries:
            break
        if not isinstance(next_page, str) or not next_page.startswith("?"):
            raise RuntimeError(
                f"registry lookup returned invalid pagination for {crate_name}"
            )
        query = next_page
    return tuple(versions)


def _cargo_precise_version(crate_name: str, max_version: str) -> str:
    """Resolve a hold ceiling to an exact published Cargo version."""
    parts = [part for part in max_version.strip().lstrip("v").split(".") if part]
    if len(parts) == 3:
        return ".".join(parts)

    try:
        candidates = [
            version
            for version in _cargo_registry_versions(crate_name)
            if version_satisfies_ceiling(version, max_version)
        ]
    except RuntimeError as exc:
        raise RuntimeError(
            f"hold: failed to resolve {crate_name} ceiling {max_version!r} "
            "from the Cargo registry; use an exact three-component "
            "max_version for a published release"
        ) from exc
    if candidates:
        return max(candidates, key=semver_tuple)
    raise RuntimeError(
        f"hold: no published {crate_name} version satisfies ceiling "
        f"{max_version!r}; use an exact three-component max_version for a "
        "published release"
    )


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
        audit_override = _AUDIT_VERSION_OVERRIDES.get(change.name)
        if audit_override is not None:
            kept.append(Change(change.name, change.from_ver, audit_override))
            continue
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
        audit_override = _AUDIT_VERSION_OVERRIDES.get(crate_name)
        rule = _resolve_crate_hold(ws, crate_name)
        if audit_override is not None:
            violating = [version for version in versions if version != audit_override]
            target = audit_override
            ceiling_label = f"audit override {audit_override}"
            expected_landed = f"== {audit_override}"
        elif rule is not None:
            violating = [
                version
                for version in versions
                if not version_satisfies_ceiling(version, rule.max_version)
            ]
            candidates = [
                version
                for version in versions
                if version_satisfies_ceiling(version, rule.max_version)
            ]
            target = (
                max(candidates, key=semver_tuple)
                if candidates
                else _cargo_precise_version(crate_name, rule.max_version)
            )
            ceiling_label = f"hold ceiling {rule.max_version}"
            expected_landed = f"<= {rule.max_version}"
        else:
            continue
        if not violating:
            continue
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
                    f"back to {ceiling_label} (cargo): {proc.stderr.strip()}"
                )
        landed = _cargo_resolved_versions(ws).get(crate_name, ())
        if audit_override is not None:
            still_invalid = not landed or any(
                version != audit_override for version in landed
            )
        elif rule is not None:
            still_invalid = not landed or any(
                not version_satisfies_ceiling(version, rule.max_version)
                for version in landed
            )
        else:
            still_invalid = True
        if still_invalid:
            raise RuntimeError(
                f"hold: {crate_name} still at {', '.join(landed) or violating[-1]} "
                f"after clamp attempt, expected {expected_landed} (cargo)"
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
