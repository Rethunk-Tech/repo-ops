"""Yarn Berry check/apply with isolated workspace snapshots."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_js.overrides import overrides_audit
from repo_ops_js.workspaces import corepack_prepare
from repo_ops_lockfiles.parsers import versions_yarn_lock
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import (
    YARN_ISOLATED_COPY_SKIP,
    YARN_ISOLATED_YARN_SKIP,
    resolve_timeout_apply,
)

FILE_PROTOCOL_RE = re.compile(r"^file:(\.\./.+?)(?:#.*)?$")
_STDERR_TAIL_LINES = 12
_YARN_UPDATE_LATEST_ENV = "REPO_OPS_YARN_UPDATE_LATEST"
_YARN_TARGETED_BUMP_ENV = "REPO_OPS_YARN_TARGETED_BUMP"
_TIMEOUT_NPM_INFO = 60
_TRUE_VALUES = ("1", "true", "yes")


def _yarn_update_latest_enabled() -> bool:
    return os.environ.get(_YARN_UPDATE_LATEST_ENV, "").strip().lower() in _TRUE_VALUES


def _yarn_targeted_bump_enabled() -> bool:
    return os.environ.get(_YARN_TARGETED_BUMP_ENV, "").strip().lower() in _TRUE_VALUES


def _yarn_up_cmd() -> list[str]:
    """In-range resolution refresh: ``yarn up -R '*'`` in both modes.

    Bare ``yarn up`` (no patterns) is a no-op in Yarn 4 — it resolves and
    installs but upgrades nothing, so conservative mode never moved a single
    in-range bump (issue #114). ``-R '*'`` re-resolves every dependency to the
    newest version satisfying the existing package.json range without touching
    the range itself. Latest mode differs downstream, not here: it adds
    behind-latest reporting (#110) and targeted range-breaking bumps (#111).
    """
    return ["yarn", "up", "-R", "*"]


def _stderr_tail(proc: subprocess.CompletedProcess[str]) -> str:
    combined = (
        (proc.stderr or "")
        + ("\n" if proc.stderr and proc.stdout else "")
        + (proc.stdout or "")
    )
    lines = [ln for ln in combined.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = "\n".join(lines[-_STDERR_TAIL_LINES:])
    return f"{tail}\n" if tail else ""


def _command_failure(
    label: str, proc: subprocess.CompletedProcess[str]
) -> RuntimeError:
    detail = _stderr_tail(proc)
    msg = proc.stderr.strip() or label
    if detail and detail.strip() not in msg:
        msg = f"{msg}\n{detail.rstrip()}"
    return RuntimeError(msg)


def _isolated_yarn_ignore(src: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in YARN_ISOLATED_COPY_SKIP}
    if Path(src).name == ".yarn":
        ignored.update(name for name in names if name in YARN_ISOLATED_YARN_SKIP)
    return ignored


def _package_json_paths(ws_path: Path) -> list[Path]:
    root_pkg = ws_path / "package.json"
    if not root_pkg.is_file():
        return []
    paths = [root_pkg]
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return paths
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        patterns = workspaces.get("packages") or []
    elif isinstance(workspaces, list):
        patterns = workspaces
    else:
        patterns = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        for match in ws_path.glob(pattern):
            pkg = match / "package.json" if match.is_dir() else match
            if pkg.is_file() and pkg not in paths:
                paths.append(pkg)
    return paths


def _file_protocol_targets(ws_path: Path) -> list[Path]:
    targets: list[Path] = []
    seen: set[Path] = set()
    for pkg_json in _package_json_paths(ws_path):
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        base = pkg_json.parent
        for section in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            deps = data.get(section) or {}
            if not isinstance(deps, dict):
                continue
            for spec in deps.values():
                if not isinstance(spec, str):
                    continue
                m = FILE_PROTOCOL_RE.match(spec.strip())
                if not m:
                    continue
                target = (base / m.group(1)).resolve()
                if target.exists() and target not in seen:
                    seen.add(target)
                    targets.append(target)
    return targets


def _layout_root(source_root: Path, extras: list[Path]) -> Path:
    if not extras:
        return source_root.parent
    candidates = [source_root, *extras]
    common_parts: list[str] = []
    split_paths = [p.parts for p in candidates]
    for parts in zip(*split_paths, strict=False):
        if len(set(parts)) == 1:
            common_parts.append(parts[0])
        else:
            break
    if not common_parts:
        return source_root.parent
    common = Path(*common_parts)
    if common in candidates:
        return common.parent
    return common


def _copy_tree(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src,
        dest,
        symlinks=True,
        ignore=_isolated_yarn_ignore,
        ignore_dangling_symlinks=True,
    )


def _copy_yarn_workspace_to_temp(ws: Workspace, temp_root: Path) -> Path:
    try:
        rel_ws = ws.path.relative_to(ws.git_repo)
        source_root = ws.git_repo
    except ValueError:
        rel_ws = Path(".")
        source_root = ws.path
    extras = [
        p
        for p in _file_protocol_targets(ws.path)
        if p.is_dir() and p != source_root and not p.is_relative_to(source_root)
    ]
    layout_root = _layout_root(source_root, extras)
    trees = {source_root, *extras}
    for root in trees:
        rel = root.relative_to(layout_root)
        dest = temp_root / rel
        if dest.exists():
            continue
        _copy_tree(root, dest)
    dest_ws_root = (temp_root / source_root.relative_to(layout_root)).resolve()
    return (dest_ws_root / rel_ws).resolve()


def _direct_dep_names(ws_path: Path) -> set[str]:
    """Direct dependency names declared across the workspace's package.json files."""
    names: set[str] = set()
    for pkg_json in _package_json_paths(ws_path):
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for section in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
        ):
            deps = data.get(section)
            if isinstance(deps, dict):
                names.update(k for k in deps if isinstance(k, str))
    return names


def _resolved_versions(lock_path: Path) -> dict[str, str]:
    """name -> highest resolved version from a yarn.lock (handles duplicate ranges)."""
    resolved: dict[str, str] = {}
    for entry in versions_yarn_lock(lock_path):
        name, _, ver = entry.rpartition("@")
        if not name or not ver:
            continue
        prev = resolved.get(name)
        try:
            if prev is None or semver_tuple(ver) > semver_tuple(prev):
                resolved[name] = ver
        except (TypeError, ValueError):
            resolved.setdefault(name, ver)
    return resolved


def _latest_published(mdir: Path, name: str) -> str | None:
    """Latest published version via ``yarn npm info`` (None on any failure)."""
    try:
        proc = run(
            ["yarn", "npm", "info", name, "--json"], mdir, timeout=_TIMEOUT_NPM_INFO
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    dist_tags = data.get("dist-tags")
    if isinstance(dist_tags, dict):
        latest = dist_tags.get("latest")
        if isinstance(latest, str):
            return latest
    ver = data.get("version")
    return ver if isinstance(ver, str) else None


def behind_after_up(mdir: Path) -> list[Change]:
    """Direct deps with a newer published version that ``yarn up`` did not apply.

    yarn's resolver (especially under ``yarn up -R '*'``) can keep a package below
    its newest release for peer/group constraints, and repo-ops lands exactly what
    yarn produces — silently. This surfaces those gaps (issue #110) as ``behind_latest``
    so a supersede run reads honestly instead of "everything is current".

    Only runs in latest mode (``--latest`` / REPO_OPS_YARN_UPDATE_LATEST), where the
    gap is meaningful and the extra registry lookups are warranted. Resilient: any
    ``yarn npm info`` failure skips that package rather than failing the run.
    """
    if not _yarn_update_latest_enabled():
        return []
    lock = mdir / "yarn.lock"
    if not lock.is_file():
        return []
    resolved = _resolved_versions(lock)
    behind: list[Change] = []
    for name in sorted(_direct_dep_names(mdir)):
        cur = resolved.get(name)
        if not cur:
            continue
        latest = _latest_published(mdir, name)
        if not latest or latest == cur:
            continue
        try:
            if semver_tuple(latest) > semver_tuple(cur):
                behind.append(Change(name, cur, latest))
        except (TypeError, ValueError):
            continue
    return behind


def targeted_bump_behind(
    mdir: Path, behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Per-package ``yarn up <pkg>@latest`` fallback for still-behind deps (issue #111).

    Option 2 of #110: rather than only reporting the gap ``behind_after_up`` found,
    optionally *move* it. For each still-behind direct dep we snapshot the workspace's
    lockfile/manifests, run a targeted ``yarn up <pkg>@latest`` + ``yarn install``, and
    keep the bump only if install succeeds **and** the resolved version actually advanced
    (guards the ghost-bump case where the lock churns but the resolver holds the old
    version). On any failure or no-op we restore the snapshot, leaving that package behind.

    Opt-in (``REPO_OPS_YARN_TARGETED_BUMP`` / ``--yarn-targeted-bump``) and reversible
    per-package: a bump that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version landed.
    """
    if not behind or not _yarn_targeted_bump_enabled():
        return [], behind
    lock = mdir / "yarn.lock"
    if not lock.is_file():
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        snap = snapshot_workspace_files(mdir, "yarn")
        up = run(
            ["yarn", "up", f"{change.name}@latest"],
            mdir,
            timeout=resolve_timeout_apply(),
        )
        install = (
            run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())
            if up.returncode == 0
            else None
        )
        landed = _resolved_versions(lock).get(change.name) if install else None
        advanced = False
        if landed:
            try:
                advanced = semver_tuple(landed) > semver_tuple(change.from_ver)
            except (TypeError, ValueError):
                advanced = landed != change.from_ver
        if landed and advanced and install and install.returncode == 0:
            applied.append(Change(change.name, change.from_ver, landed))
        else:
            restore_workspace_snapshot(mdir, snap, "yarn")
            still_behind.append(change)
    return applied, still_behind


def check_yarn_snapshot(
    ws: Workspace,
) -> tuple[list[Change], list[dict[str, str]], list[Change]]:
    """Plan via isolated copy; conservative ``yarn up`` unless latest env is set."""
    changes: list[Change] = []
    snapshot_overrides: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="dep-refresh-yarn-") as td:
        mdir = _copy_yarn_workspace_to_temp(ws, Path(td))
        before_snap = snapshot_workspace_files(mdir, "yarn")
        corepack_prepare("yarn", mdir)
        proc = run(_yarn_up_cmd(), mdir, timeout=resolve_timeout_apply())
        if proc.returncode != 0:
            raise _command_failure("yarn up failed", proc)
        proc2 = run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())
        if proc2.returncode != 0:
            raise _command_failure("yarn install failed", proc2)
        after_snap = snapshot_workspace_files(mdir, "yarn")
        changes = diff_workspace_snapshots(before_snap, after_snap, "yarn")
        pkg_path = mdir / "package.json"
        pkg_before = before_snap.get(pkg_path, b"")
        pkg_after = after_snap.get(pkg_path, b"")
        if pkg_before != pkg_after and not any(
            c.name == "package.json" for c in changes
        ):
            changes.append(Change("package.json", "changed", "changed"))
        snapshot_overrides = overrides_audit(mdir, "yarn")
        behind = behind_after_up(mdir)
        # Optionally move (not just report) the gap; applied bumps become planned
        # changes so plan previews what apply will land (issue #111).
        applied, behind = targeted_bump_behind(mdir, behind)
        for c in applied:
            if not any(existing.name == c.name for existing in changes):
                changes.append(c)
    return changes, snapshot_overrides, behind


def apply_yarn(ws: Workspace) -> list[Change]:
    """Apply in workspace; matches check policy (see ``_yarn_up_cmd``).

    Returns the direct deps a latest-mode ``yarn up`` left behind (issue #110);
    empty in conservative mode or when nothing is behind. With ``--yarn-targeted-bump``
    (issue #111), each still-behind dep gets a reversible targeted bump first, so only
    the deps that truly could not move are returned.
    """
    corepack_prepare("yarn", ws.path)
    proc = run(_yarn_up_cmd(), ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise _command_failure("yarn up failed", proc)
    proc2 = run(["yarn", "install"], ws.path, timeout=resolve_timeout_apply())
    if proc2.returncode != 0:
        raise _command_failure("yarn install failed", proc2)
    behind = behind_after_up(ws.path)
    _, behind = targeted_bump_behind(ws.path, behind)
    return behind
