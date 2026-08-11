"""Deno upgrade check/apply."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from functools import cache
from pathlib import Path

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
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
from repo_ops_lockfiles.parsers import versions_deno_lock
from repo_ops_lockfiles.snapshots import snapshot_workspace_files
from repo_ops_models import Change, Workspace

from repo_ops_js.guards import assert_targets_resolved

_DENO_UPDATE_LATEST_ENV = "REPO_OPS_DENO_UPDATE_LATEST"
_DENO_TARGETED_BUMP_ENV = "REPO_OPS_DENO_TARGETED_BUMP"
_TRUE_VALUES = ("1", "true", "yes", "on")
_DENO_CONFIG_FILENAMES = ("deno.json", "deno.jsonc")
_DENO_IMPORT_MAP_FILENAMES = (
    "import_map.json",
    "import-map.json",
    "import_map.jsonc",
    "import-map.jsonc",
)
_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}
_SEMVER_RE = re.compile(
    r"^[vV]?\d+(?:\.\d+)*(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _process_env_cli_override(env_var: str) -> str | None:
    """Return a process value changed after the ambient environment snapshot."""
    ambient = ambient_env()
    if ambient is None:
        return None
    process_value = os.environ.get(env_var)
    if env_var not in ambient:
        if process_value is None:
            return None
        setting = env_var.removeprefix("REPO_OPS_").lower()
        global_value = load_global_config().get(setting)
        if global_value is True:
            global_value = "1"
        elif global_value is False:
            global_value = "0"
        return (
            None
            if global_value is not None and process_value == str(global_value)
            else process_value
        )
    return process_value if process_value != ambient[env_var] else None


def _deno_setting(name: str, env_var: str, ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        name,
        cli_value=_process_env_cli_override(env_var),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _TRUE_VALUES


def _deno_update_latest_enabled(ws: Workspace | None = None) -> bool:
    return _deno_setting("deno_update_latest", _DENO_UPDATE_LATEST_ENV, ws)


def _deno_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    return _deno_setting("deno_targeted_bump", _DENO_TARGETED_BUMP_ENV, ws)


def _parseable_semver_tuple(version: str) -> tuple[int, ...] | None:
    if not _SEMVER_RE.fullmatch(version):
        return None
    return semver_tuple(version)


def _deno_precise_version(version: str) -> str:
    """Normalize a numeric Deno version without inventing a patch."""
    normalized = version.strip().lstrip("vV")
    parts = normalized.split(".")
    if not parts or len(parts) > 3 or any(not part.isdigit() for part in parts):
        return normalized
    return ".".join(parts)


def _deno_import_map_paths(ws: Workspace) -> list[Path]:
    """Return import maps usable by Deno in this workspace."""
    workspace_root = Path(ws.path).resolve()
    default_paths = {workspace_root / name for name in _DENO_IMPORT_MAP_FILENAMES}
    config_paths: set[Path] = set()
    safe_default_paths: set[Path] = set()
    for default_path in default_paths:
        resolved_path = default_path.resolve()
        try:
            resolved_path.relative_to(workspace_root)
        except ValueError:
            continue
        safe_default_paths.add(resolved_path)
    for config_name in _DENO_CONFIG_FILENAMES:
        config_path = workspace_root / config_name
        if not config_path.is_file():
            continue
        try:
            config_text = config_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(r'"importMap"\s*:\s*"([^"]+)"', config_text):
            import_map_path = (workspace_root / match.group(1)).resolve()
            try:
                import_map_path.relative_to(workspace_root)
            except ValueError:
                continue
            config_paths.add(import_map_path)
    existing_config_paths = {path for path in config_paths if path.is_file()}
    paths = sorted(
        (safe_default_paths | config_paths),
        key=lambda path: path.as_posix(),
    )
    return [
        *sorted(existing_config_paths, key=lambda path: path.as_posix()),
        *(
            path
            for path in paths
            if path.is_file() and path not in existing_config_paths
        ),
    ]


def _deno_update_command(ws: Workspace) -> list[str]:
    """Return a Deno update command with the workspace import map."""
    command = ["deno", "update"]
    import_maps = _deno_import_map_paths(ws)
    if import_maps:
        command.extend(["--import-map", str(import_maps[0])])
    return command


def _deno_snapshot_paths(ws: Workspace) -> set[Path]:
    workspace_root = Path(ws.path).resolve()
    paths = set(_deno_import_map_paths(ws))
    for config_name in _DENO_CONFIG_FILENAMES:
        config_path = workspace_root / config_name
        try:
            config_path.resolve().relative_to(workspace_root)
        except ValueError:
            continue
        if config_path.is_file():
            paths.add(config_path)
    return paths


def _snapshot_deno_workspace(ws: Workspace) -> dict[Path, bytes]:
    snapshot = dict(snapshot_workspace_files(ws.path, "deno"))
    for path in _deno_snapshot_paths(ws):
        snapshot[path] = path.read_bytes()
    return snapshot


def _restore_deno_workspace(ws: Workspace, before: dict[Path, bytes]) -> None:
    after = _snapshot_deno_workspace(ws)
    for path in set(before) | set(after):
        if path in before:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before[path])
        elif path.exists():
            path.unlink()


def set_deno_audit_version_overrides(overrides: dict[str, str]) -> None:
    _AUDIT_VERSION_OVERRIDES.clear()
    _AUDIT_VERSION_OVERRIDES.update(overrides)


def clear_deno_audit_version_overrides() -> None:
    _AUDIT_VERSION_OVERRIDES.clear()


def apply_deno_audit_version_overrides(ws: Workspace) -> None:
    for name, version in _AUDIT_VERSION_OVERRIDES.items():
        snapshot = _snapshot_deno_workspace(ws)
        proc = run(
            [
                *_deno_update_command(ws),
                _deno_version_target(name, version),
            ],
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        landed = _landed_deno_version(ws, name) if proc.returncode == 0 else None
        if landed == _deno_precise_version(version):
            continue
        _restore_deno_workspace(ws, snapshot)
        detail = proc.stderr.strip() if proc.returncode != 0 else "target not landed"
        raise RuntimeError(f"deno audit override failed for {name}@{version}: {detail}")


def _deno_version_target(name: str, version: str) -> str:
    return f"{name}@{_deno_precise_version(version)}"


def _deno_ceiling_target(name: str, version: str) -> str:
    return f"{name}@<={_deno_precise_version(version)}"


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


def _resolve_deno_hold(ws: Workspace, package: str) -> HoldRule | None:
    """Return the active hold rule for *package* in this workspace, if any."""
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


def _filter_held_deno_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    """Drop changes whose target exceeds an active package hold."""
    return [
        change
        for change in changes
        if _AUDIT_VERSION_OVERRIDES.get(change.name) is not None
        or (rule := _resolve_deno_hold(ws, change.name)) is None
        or version_satisfies_ceiling(change.to, rule.max_version)
    ]


def check_deno(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Return pending upgrades for a Deno workspace.

    Runs ``deno outdated`` (Deno ≥ 2.x) and parses its tabular output.
    Supports both the box-drawing table format and plain-text column format::

        Package           Current  Update   Latest
        jsr:@std/assert   0.221.0  0.226.0  0.226.0
        npm:chalk         5.2.0    5.3.0    5.3.0

    Returns ``(changes, behind_latest)``. By default (conservative), ``Update``
    is the actionable target and packages where only ``Latest`` is newer go to
    ``behind_latest``. With ``REPO_OPS_DENO_UPDATE_LATEST`` set, ``Latest`` is
    the actionable target and ``behind_latest`` is empty.

    Falls back to empty lists with a WARN when ``deno`` is absent so the
    workspace degrades gracefully (report-only, no error).
    """
    if not shutil.which("deno"):
        print(f"WARN {ws.path}: deno not on PATH", file=sys.stderr)
        return [], []
    proc = run(
        ["deno", "outdated"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "deno outdated failed")
    changes, behind_latest = _parse_deno_outdated(proc.stdout, ws)
    return (
        _filter_held_deno_changes(ws, changes),
        _filter_held_deno_changes(ws, behind_latest),
    )


def _parse_deno_outdated(
    stdout: str, ws: Workspace | None = None
) -> tuple[list[Change], list[Change]]:
    """Parse ``deno outdated`` stdout into ``(changes, behind_latest)``."""
    want_latest = _deno_update_latest_enabled(ws)
    changes: list[Change] = []
    behind_latest: list[Change] = []
    # Plain text: "name  current  update  latest" OR "name  current  latest"
    _PLAIN_4 = re.compile(r"^(\S+)\s+([\d][^\s]*)\s+([\d][^\s]*)\s+([\d][^\s]*)")
    _PLAIN_3 = re.compile(r"^(\S+)\s+([\d][^\s]*)\s+([\d][^\s]*)")
    # Vertical-bar character used in Deno's box-drawing table output.
    _VBAR = "│"  # │ (BOX DRAWINGS LIGHT VERTICAL)
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _VBAR in stripped:
            # Box-drawing table row: split on │ to get cell contents.
            # Border rows (┌─┬─┐, ├─┼─┤, └─┴─┘) do not contain │ so they
            # fall through to the plain-text branch which won't match them.
            parts = [p.strip() for p in stripped.split(_VBAR) if p.strip()]
            if len(parts) < 3:
                continue
            name = parts[0]
            if name.lower() in ("package", "name"):
                continue  # header row
            cur = parts[1]
            if len(parts) >= 4:
                update_ver, latest = parts[2], parts[3]
            else:
                # Legacy 3-column table: treat the last column as both Update and Latest.
                update_ver = latest = parts[-1]
            if not cur or not re.match(r"[\d]", cur):
                continue
            target = latest if want_latest else update_ver
            if target and cur != target:
                changes.append(Change(name, cur, target))
            elif not want_latest and latest and cur != latest and update_ver == cur:
                behind_latest.append(Change(name, cur, latest))
            continue
        m4 = _PLAIN_4.match(stripped)
        if m4:
            name, cur, update_ver, latest = (
                m4.group(1),
                m4.group(2),
                m4.group(3),
                m4.group(4),
            )
            if name.lower() in ("package", "name"):
                continue
            target = latest if want_latest else update_ver
            if cur != target:
                changes.append(Change(name, cur, target))
            elif not want_latest and cur != latest and update_ver == cur:
                behind_latest.append(Change(name, cur, latest))
            continue
        m = _PLAIN_3.match(stripped)
        if m:
            name, cur, latest = m.group(1), m.group(2), m.group(3)
            if name.lower() in ("package", "name") or cur == latest:
                continue
            # 3-column plain: no distinct Update — treat Latest as the only target.
            changes.append(Change(name, cur, latest))
    return changes, behind_latest


def _landed_deno_version(ws: Workspace, name: str) -> str | None:
    """Return the resolved lockfile version for a package name."""
    versions = _deno_lock_versions(ws, name)
    if not versions:
        return None
    parseable = [
        (parsed, version)
        for version in versions
        if (parsed := _parseable_semver_tuple(version)) is not None
    ]
    if parseable:
        return max(parseable, key=lambda item: item[0])[1]
    return max(versions)


def _deno_lock_versions(ws: Workspace, name: str) -> list[str]:
    """Return all resolved lockfile versions for a package name."""
    prefix = f"{name}@"
    versions = [
        resolved[len(prefix) :].strip()
        for resolved in versions_deno_lock(Path(ws.path) / "deno.lock")
        if resolved.startswith(prefix)
    ]
    return [version for version in versions if version]


def _deno_outdated_versions(ws: Workspace, name: str) -> list[str]:
    """Return version candidates for *name* from Deno's JSON report."""
    proc = run(
        ["deno", "outdated", "--json"],
        ws.path,
        timeout=resolve_timeout_apply(),
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout)
    except (TypeError, json.JSONDecodeError):
        return []

    candidates: set[str] = set()

    def collect_versions(value: object) -> None:
        if isinstance(value, str) and _parseable_semver_tuple(value) is not None:
            candidates.add(value)
        elif isinstance(value, list):
            for item in value:
                collect_versions(item)

    def visit(value: object, package_name: str | None = None) -> None:
        if isinstance(value, dict):
            identity = package_name
            for key in ("name", "package", "specifier", "id"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    identity = candidate
                    break
            if identity == name:
                for key in (
                    "current",
                    "update",
                    "wanted",
                    "latest",
                    "version",
                    "versions",
                    "available",
                ):
                    collect_versions(value.get(key))
            for key, child in value.items():
                visit(child, name if key == name else identity)
        elif isinstance(value, list):
            for item in value:
                visit(item, package_name)

    visit(payload)
    return sorted(
        candidates,
        key=lambda version: _parseable_semver_tuple(version) or (),
    )


def _highest_deno_version(versions: list[str], max_version: str) -> str | None:
    compatible = [
        (parsed, version)
        for version in versions
        if (parsed := _parseable_semver_tuple(version)) is not None
        and version_satisfies_ceiling(version, max_version)
    ]
    if not compatible:
        return None
    return max(compatible, key=lambda item: item[0])[1]


def _deno_lock_package_names(ws: Workspace) -> set[str]:
    """Return package identities recorded in the resolved Deno lockfile."""
    names: set[str] = set()
    for resolved in versions_deno_lock(Path(ws.path) / "deno.lock"):
        name, separator, version = resolved.rpartition("@")
        if separator and name and version:
            names.add(name)
    return names


def targeted_bump_behind(
    ws: Workspace, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Run a reversible per-package ``deno update`` for behind-latest deps."""
    if not behind or not _deno_targeted_bump_enabled(ws):
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        audit_override = _AUDIT_VERSION_OVERRIDES.get(change.name)
        hold = _resolve_deno_hold(ws, change.name)
        if (
            audit_override is None
            and hold is not None
            and not version_satisfies_ceiling(change.to, hold.max_version)
        ):
            still_behind.append(change)
            continue
        snapshot = _snapshot_deno_workspace(ws)
        cmd = _deno_update_command(ws)
        if audit_override is not None:
            cmd.append(_deno_version_target(change.name, audit_override))
        elif hold is None:
            if _deno_update_latest_enabled(ws):
                cmd.append("--latest")
            cmd.append(change.name)
        else:
            cmd.append(_deno_version_target(change.name, change.to))
        proc = run(cmd, ws.path, timeout=resolve_timeout_apply())
        advanced = False
        if proc.returncode == 0:
            advanced = _snapshot_deno_workspace(ws) != snapshot
        if advanced:
            landed = _landed_deno_version(ws, change.name)
            if landed is None:
                _restore_deno_workspace(ws, snapshot)
                still_behind.append(change)
                continue
            if (
                audit_override is None
                and hold is not None
                and not version_satisfies_ceiling(landed, hold.max_version)
            ):
                _restore_deno_workspace(ws, snapshot)
                still_behind.append(change)
                continue
            landed_tuple = _parseable_semver_tuple(landed)
            from_tuple = _parseable_semver_tuple(change.from_ver)
            moved_up = (
                landed != change.from_ver
                if landed_tuple is None or from_tuple is None
                else landed_tuple > from_tuple
            )
            if moved_up:
                resolved_change = Change(change.name, change.from_ver, landed)
                try:
                    assert_targets_resolved(
                        ws,
                        [
                            change
                            if audit_override is None
                            else Change(
                                change.name,
                                change.from_ver,
                                audit_override,
                            )
                        ],
                        manager="deno",
                        fallback_resolver=_landed_deno_version,
                    )
                except RuntimeError:
                    _restore_deno_workspace(ws, snapshot)
                    still_behind.append(change)
                else:
                    applied.append(resolved_change)
                continue
            _restore_deno_workspace(ws, snapshot)
            still_behind.append(change)
        else:
            _restore_deno_workspace(ws, snapshot)
            still_behind.append(change)
    return applied, still_behind


def _enforce_deno_version_ceilings(
    ws: Workspace, candidate_names: list[str] | None = None
) -> None:
    """Clamp held packages that remain above their ceiling after an update."""
    candidates = set(candidate_names or ())
    candidates.update(_deno_lock_package_names(ws))
    for name in sorted(candidates):
        audit_override = _AUDIT_VERSION_OVERRIDES.get(name)
        rule = _resolve_deno_hold(ws, name)
        if audit_override is not None or rule is None:
            continue
        resolved = _landed_deno_version(ws, name)
        if resolved is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        snapshot = _snapshot_deno_workspace(ws)
        proc = run(
            [
                *_deno_update_command(ws),
                _deno_ceiling_target(name, rule.max_version),
            ],
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        landed = _landed_deno_version(ws, name) if proc.returncode == 0 else None
        if landed is not None and version_satisfies_ceiling(landed, rule.max_version):
            continue

        _restore_deno_workspace(ws, snapshot)
        candidates = [
            *_deno_outdated_versions(ws, name),
            *_deno_lock_versions(ws, name),
        ]
        target = _highest_deno_version(candidates, rule.max_version)
        if target is not None:
            proc = run(
                [*_deno_update_command(ws), _deno_version_target(name, target)],
                ws.path,
                timeout=resolve_timeout_apply(),
            )
            landed = _landed_deno_version(ws, name) if proc.returncode == 0 else None
        if (
            target is None
            or landed is None
            or not version_satisfies_ceiling(landed, rule.max_version)
        ):
            _restore_deno_workspace(ws, snapshot)
            raise RuntimeError(
                f"hold: failed to clamp {name} to <= {rule.max_version} (deno); "
                "set an exact three-component max_version if the ceiling is "
                "not published"
            )


def apply_deno(ws: Workspace) -> list[Change]:
    """Run ``deno outdated --update`` to upgrade dependencies.

    Requires Deno ≥ 2.x; ``deno outdated --update`` rewrites ``deno.lock``
    and import maps in-place. When ``REPO_OPS_DENO_UPDATE_LATEST`` is unset,
    Deno's default update stays range-respecting (Update column); when set,
    ``--latest`` is passed so cross-range bumps are applied.

    After a successful update, planned targets are verified via
    :func:`assert_targets_resolved` (ghost-bump guard parity with Node PMs).
    Returns ``behind_latest`` gaps that remain after the update.
    """
    if not shutil.which("deno"):
        raise RuntimeError(f"deno not on PATH for {ws.path}")
    planned, behind = check_deno(ws)
    cmd = ["deno", "outdated", "--update"]
    if _deno_update_latest_enabled(ws):
        cmd.append("--latest")
    proc = run(
        cmd,
        ws.path,
        timeout=resolve_timeout_apply(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "deno outdated --update failed")
    guardable = [
        ch
        for ch in planned
        if (ch.to or "").strip() not in ("", "?", "latest") and (ch.name or "").strip()
    ]
    if guardable:
        assert_targets_resolved(
            ws,
            guardable,
            manager="deno",
            fallback_resolver=_landed_deno_version,
        )
    _, behind = targeted_bump_behind(ws, behind)
    _enforce_deno_version_ceilings(ws, [change.name for change in [*planned, *behind]])
    return behind
