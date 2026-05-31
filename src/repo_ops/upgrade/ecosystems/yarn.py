"""Yarn Berry check/apply with isolated workspace snapshots."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from repo_ops.core.process import run
from repo_ops.upgrade.constants import (
    TIMEOUT_APPLY,
    YARN_ISOLATED_COPY_SKIP,
    YARN_ISOLATED_YARN_SKIP,
)
from repo_ops.upgrade.models import Change, Workspace
from repo_ops.upgrade.workspaces import corepack_prepare

from .node import overrides_audit
from .node_lockfiles import diff_workspace_snapshots, snapshot_workspace_files


def _isolated_yarn_ignore(src: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in YARN_ISOLATED_COPY_SKIP}
    if Path(src).name == ".yarn":
        ignored.update(name for name in names if name in YARN_ISOLATED_YARN_SKIP)
    return ignored


def _copy_yarn_workspace_to_temp(ws: Workspace, temp_root: Path) -> Path:
    try:
        rel_ws = ws.path.relative_to(ws.git_repo)
        source_root = ws.git_repo
    except ValueError:
        rel_ws = Path(".")
        source_root = ws.path
    dest_root = temp_root / source_root.name
    shutil.copytree(
        source_root,
        dest_root,
        symlinks=True,
        ignore=_isolated_yarn_ignore,
        ignore_dangling_symlinks=True,
    )
    return (dest_root / rel_ws).resolve()


def check_yarn_snapshot(ws: Workspace) -> tuple[list[Change], list[dict[str, str]]]:
    changes: list[Change] = []
    snapshot_overrides: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="dep-refresh-yarn-") as td:
        mdir = _copy_yarn_workspace_to_temp(ws, Path(td))
        before_snap = snapshot_workspace_files(mdir, "yarn")
        corepack_prepare("yarn", mdir)
        proc = run(["yarn", "up", "-R", "*"], mdir, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "yarn up failed")
        proc2 = run(["yarn", "install"], mdir, timeout=TIMEOUT_APPLY)
        if proc2.returncode != 0:
            raise RuntimeError(proc2.stderr.strip() or "yarn install failed")
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
    corepack_prepare("yarn", ws.path)
    proc = run(["yarn", "up", "-R", "*"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yarn up failed")
    proc2 = run(["yarn", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "yarn install failed")
