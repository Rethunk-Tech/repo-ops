"""Yarn Berry check/apply with isolated workspace snapshots."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_js.overrides import overrides_audit
from repo_ops_js.workspaces import corepack_prepare
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import (
    TIMEOUT_APPLY,
    YARN_ISOLATED_COPY_SKIP,
    YARN_ISOLATED_YARN_SKIP,
)

FILE_PROTOCOL_RE = re.compile(r"^file:(\.\./.+?)(?:#.*)?$")
_STDERR_TAIL_LINES = 12
_YARN_UPDATE_LATEST_ENV = "REPO_OPS_YARN_UPDATE_LATEST"


def _yarn_update_latest_enabled() -> bool:
    return os.environ.get(_YARN_UPDATE_LATEST_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _yarn_up_cmd() -> list[str]:
    """Conservative in-range bumps by default; ``yarn up -R '*'`` when latest is opted in."""
    if _yarn_update_latest_enabled():
        return ["yarn", "up", "-R", "*"]
    return ["yarn", "up"]


def _stderr_tail(proc) -> str:
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


def _command_failure(label: str, proc) -> RuntimeError:
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


def check_yarn_snapshot(ws: Workspace) -> tuple[list[Change], list[dict[str, str]]]:
    """Plan via isolated copy; conservative ``yarn up`` unless latest env is set."""
    changes: list[Change] = []
    snapshot_overrides: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="dep-refresh-yarn-") as td:
        mdir = _copy_yarn_workspace_to_temp(ws, Path(td))
        before_snap = snapshot_workspace_files(mdir, "yarn")
        corepack_prepare("yarn", mdir)
        proc = run(_yarn_up_cmd(), mdir, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise _command_failure("yarn up failed", proc)
        proc2 = run(["yarn", "install"], mdir, timeout=TIMEOUT_APPLY)
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
    return changes, snapshot_overrides


def apply_yarn(ws: Workspace) -> None:
    """Apply in workspace; matches check policy (see ``_yarn_up_cmd``)."""
    corepack_prepare("yarn", ws.path)
    proc = run(_yarn_up_cmd(), ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise _command_failure("yarn up failed", proc)
    proc2 = run(["yarn", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise _command_failure("yarn install failed", proc2)
