"""Ecosystem check/apply dispatch."""

from __future__ import annotations

from collections.abc import Callable

from repo_ops.upgrade.models import Change, Workspace, WorkspaceResult
from repo_ops.upgrade.workspaces import corepack_prepare

from . import go, python, rust, yarn
from .node import (
    apply_bun,
    apply_npm,
    apply_pnpm,
    check_bun,
    check_npm,
    check_pnpm,
    overrides_audit,
)

CHECKERS: dict[str, Callable[[Workspace], list[Change]]] = {
    "bun": check_bun,
    "npm": check_npm,
    "pnpm": check_pnpm,
    "yarn": yarn.check_yarn,
    "go": go.check_go,
    "rust": rust.check_rust,
    "uv": python.check_uv,
    "poetry": python.check_poetry,
    "pip-tools": python.check_pip_tools,
    "hatch": python.check_hatch,
}

APPLIERS: dict[str, Callable[[Workspace], None]] = {
    "bun": apply_bun,
    "yarn": yarn.apply_yarn,
    "npm": apply_npm,
    "pnpm": apply_pnpm,
    "go": go.apply_go,
    "rust": rust.apply_rust,
    "uv": python.apply_uv,
    "poetry": python.apply_poetry,
    "pip-tools": python.apply_pip_tools,
    "hatch": python.apply_hatch,
}


def check_workspace(ws: Workspace) -> WorkspaceResult:
    result = WorkspaceResult(workspace=ws)
    try:
        if ws.ecosystem == "node" and ws.manager != "yarn":
            corepack_prepare(ws.manager, ws.path)
        checker = CHECKERS.get(ws.manager)
        if not checker:
            result.status = "skip"
            result.error = f"unsupported manager {ws.manager}"
            return result
        if ws.manager == "yarn":
            changes, snap_overrides = yarn.check_yarn_snapshot(ws)
            result.changes = changes
            result.overrides_audit = snap_overrides
        else:
            result.changes = checker(ws)
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
    except Exception as exc:
        result.status = "fail"
        result.error = str(exc)
    return result


def apply_workspace(ws: Workspace) -> None:
    if ws.ecosystem == "node":
        corepack_prepare(ws.manager, ws.path)
    applier = APPLIERS.get(ws.manager)
    if not applier:
        raise RuntimeError(f"no applier for {ws.manager}")
    applier(ws)
