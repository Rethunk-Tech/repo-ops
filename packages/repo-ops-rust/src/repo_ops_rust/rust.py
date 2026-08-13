"""Rust/Cargo upgrade check/apply.

Cargo's normal ``cargo update`` stays within existing ``Cargo.toml``
constraints. The opt-in targeted bump mode updates direct crates.io
requirements one crate at a time with ``cargo update --precise``.
"""

from __future__ import annotations

import json
import re
from functools import cache
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import quote

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
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
_RUST_TARGETED_BUMP_ENV = "REPO_OPS_RUST_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")


def set_rust_audit_version_overrides(overrides: dict[str, str]) -> None:
    _AUDIT_VERSION_OVERRIDES.clear()
    _AUDIT_VERSION_OVERRIDES.update(overrides)


def clear_rust_audit_version_overrides() -> None:
    _AUDIT_VERSION_OVERRIDES.clear()


def _rust_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "rust_targeted_bump",
        cli_value=process_env_cli_override(_RUST_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUE_VALUES


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


def _cargo_resolved_versions(
    ws: Workspace, *, timeout: int | None = None
) -> dict[str, tuple[str, ...]]:
    """Return resolved Cargo package versions grouped by crate name."""
    proc = run(
        ["cargo", "metadata", "--format-version", "1"],
        ws.path,
        timeout=resolve_timeout_check() if timeout is None else timeout,
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
    timeout = resolve_timeout_apply()
    resolved = _cargo_resolved_versions(ws, timeout=timeout)
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
        landed = _cargo_resolved_versions(ws, timeout=timeout).get(crate_name, ())
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


def _cargo_version_parts(version: str) -> tuple[int, int, int]:
    match = re.search(r"\d+(?:\.\d+){0,2}", version.strip().lstrip("vV"))
    if match is None:
        return (0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


def _cargo_caret_upper_bound(parts: tuple[int, int, int]) -> tuple[int, int, int]:
    major, minor, patch = parts
    if major:
        return (major + 1, 0, 0)
    if minor:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _cargo_requirement_satisfied(version: str, requirement: str) -> bool:
    """Return whether a published Cargo version matches a simple requirement."""
    candidate = _cargo_version_parts(version)
    for alternative in requirement.split("||"):
        constraints = [part.strip() for part in alternative.split(",") if part.strip()]
        if not constraints:
            return True
        satisfied = True
        for constraint in constraints:
            match = re.match(
                r"^(<=|>=|<|>|=|~|\^)?\s*(\d+(?:\.\d+){0,2}|\*)", constraint
            )
            if match is None:
                satisfied = False
                break
            operator = match.group(1) or "^"
            raw_version = match.group(2)
            if raw_version == "*":
                continue
            parts = _cargo_version_parts(raw_version)
            if operator in ("^", "~"):
                upper = (
                    _cargo_caret_upper_bound(parts)
                    if operator == "^"
                    else (parts[0], parts[1] + 1, 0)
                )
                if not (candidate >= parts and candidate < upper):
                    satisfied = False
                    break
            elif operator == "=":
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


def _cargo_dependency_tables(
    document: dict[str, object],
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    tables: list[tuple[tuple[str, ...], dict[str, object]]] = []
    for name, value in document.items():
        if not isinstance(value, dict):
            continue
        child_path = (*path, name)
        if name in {"dependencies", "dev-dependencies", "build-dependencies"}:
            tables.append((child_path, value))
        tables.extend(_cargo_dependency_tables(value, child_path))
    return tables


def _cargo_manifest_dependencies(
    manifest: Path,
) -> dict[str, list[tuple[str, str]]]:
    document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    dependencies: dict[str, list[tuple[str, str]]] = {}
    for _, table in _cargo_dependency_tables(document):
        for name, value in table.items():
            requirement: object = value
            if isinstance(value, dict):
                if (
                    value.get("workspace") is True
                    or "path" in value
                    or "git" in value
                    or value.get("package") not in (None, name)
                ):
                    continue
                requirement = value.get("version")
            if isinstance(requirement, str):
                dependencies.setdefault(name, []).append((name, requirement))
    return dependencies


@cache
def _cargo_latest_direct_changes_cached(
    workspace_path: str,
    manifest_mtime_ns: int,
    manifest_size: int,
) -> tuple[Change, ...]:
    manifest = Path(workspace_path) / "Cargo.toml"
    dependencies = _cargo_manifest_dependencies(manifest)
    changes: list[Change] = []
    for crate_name, entries in sorted(dependencies.items()):
        latest = max(
            _cargo_registry_versions(crate_name), key=semver_tuple, default=None
        )
        if latest is None or all(
            _cargo_requirement_satisfied(latest, requirement)
            for _, requirement in entries
        ):
            continue
        changes.append(Change(crate_name, entries[0][1], latest))
    return tuple(changes)


def _cargo_latest_direct_changes(ws: Workspace) -> list[Change]:
    manifest = (ws.path / "Cargo.toml").resolve()
    stat = manifest.stat()
    return list(
        _cargo_latest_direct_changes_cached(
            str(ws.path.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
        )
    )


def _cargo_dependency_section(header: str) -> bool:
    section = header.strip().strip("[]")
    return (
        section
        in {
            "dependencies",
            "dev-dependencies",
            "build-dependencies",
        }
        or re.search(
            r"(?:^|\.)(?:dependencies|dev-dependencies|build-dependencies)(?:\.|$)",
            section,
        )
        is not None
    )


def _cargo_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    if stripped.startswith("^"):
        return f"^{latest}"
    if stripped.startswith("~"):
        return f"~{latest}"
    return latest


def _replace_cargo_requirement(
    manifest: Path,
    crate_name: str,
    old_requirement: str,
    new_requirement: str,
) -> bool:
    lines = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    section = ""
    changed = False
    for index, line in enumerate(lines):
        header = re.match(r"^\s*(\[[^\]]+\])\s*$", line)
        if header:
            section = header.group(1)
            continue
        direct_section = _cargo_dependency_section(section)
        table_section = direct_section and re.search(
            rf"\.(?:dependencies|dev-dependencies|build-dependencies)\."
            rf"(?:\"?{re.escape(crate_name)}\"?)$",
            section.strip().strip("[]"),
        )
        if table_section:
            match = re.match(r"^(\s*)version(\s*=\s*)([\"'])(.*?)(\3)(.*)$", line)
            if match and match.group(4) == old_requirement:
                lines[index] = (
                    f"{match.group(1)}version{match.group(2)}{match.group(3)}"
                    f"{new_requirement}{match.group(5)}{match.group(6)}"
                )
                changed = True
            continue
        if not direct_section:
            continue
        match = re.match(
            r"^(\s*)(\"[^\"]+\"|'[^']+'|[A-Za-z0-9_-]+)(\s*=\s*)(.*)$",
            line,
        )
        if not match:
            continue
        key = match.group(2).strip("\"'")
        if key != crate_name:
            continue
        value = match.group(4)
        if value.startswith(('"', "'")):
            quoted = re.match(r"([\"'])(.*?)(\1)(.*)$", value)
            if quoted and quoted.group(2) == old_requirement:
                lines[index] = (
                    f"{match.group(1)}{match.group(2)}{match.group(3)}"
                    f"{quoted.group(1)}{new_requirement}{quoted.group(3)}{quoted.group(4)}"
                )
                changed = True
        elif value.lstrip().startswith("{"):
            version = re.search(
                r"(\bversion\s*=\s*)([\"'])(.*?)(\2)",
                value,
            )
            if version and version.group(3) == old_requirement:
                replacement = f"{version.group(1)}{version.group(2)}{new_requirement}{version.group(4)}"
                lines[index] = (
                    line[: match.start(4)]
                    + value[: version.start()]
                    + replacement
                    + value[version.end() :]
                )
                changed = True
    if changed:
        manifest.write_text("".join(lines), encoding="utf-8")
    return changed


def _cargo_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _cargo_restore(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)


def _apply_cargo_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    manifest = ws.path / "Cargo.toml"
    lockfile = ws.path / "Cargo.lock"
    remaining: list[Change] = []
    for candidate in candidates:
        rule = _resolve_crate_hold(ws, candidate.name)
        if rule is not None and not version_satisfies_ceiling(
            candidate.to, rule.max_version
        ):
            remaining.append(candidate)
            continue
        manifest_snapshot = _cargo_snapshot(manifest)
        lock_snapshot = _cargo_snapshot(lockfile)
        requirement = candidate.from_ver
        if not _replace_cargo_requirement(
            manifest,
            candidate.name,
            requirement,
            _cargo_bumped_requirement(requirement, candidate.to),
        ):
            remaining.append(candidate)
            continue
        proc = run(
            [
                "cargo",
                "update",
                "-p",
                candidate.name,
                "--precise",
                candidate.to,
            ],
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        lock_advanced = _cargo_snapshot(lockfile) != lock_snapshot
        if proc.returncode == 0 and lock_advanced:
            continue
        _cargo_restore(manifest, manifest_snapshot)
        _cargo_restore(lockfile, lock_snapshot)
        remaining.append(candidate)
    return remaining


def check_rust(
    ws: Workspace,
) -> list[Change] | tuple[list[Change], list[Change]]:
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
    in_range = _filter_held_rust_changes(ws, changes)
    if not _rust_targeted_bump_enabled(ws):
        return in_range
    behind = _cargo_latest_direct_changes(ws)
    return in_range, behind


def apply_rust(ws: Workspace) -> list[Change] | None:
    proc = run(["cargo", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update failed")
    _enforce_version_ceilings(ws)
    if not _rust_targeted_bump_enabled(ws):
        return None
    return _apply_cargo_targeted_bumps(ws, _cargo_latest_direct_changes(ws))
