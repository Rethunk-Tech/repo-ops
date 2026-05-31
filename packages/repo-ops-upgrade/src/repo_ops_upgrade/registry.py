"""Ecosystem checker/applier registry (populated by meta _register)."""

from __future__ import annotations

from collections.abc import Callable

from repo_ops_js.overrides import overrides_audit
from repo_ops_js.workspaces import corepack_prepare
from repo_ops_models import Change, Workspace, WorkspaceResult

Checker = Callable[
    [Workspace], list[Change] | tuple[list[Change], list[dict[str, str]]]
]
Applier = Callable[[Workspace], None]

CHECKERS: dict[str, Checker] = {}
APPLIERS: dict[str, Applier] = {}


def register(manager: str, checker: Checker, applier: Applier) -> None:
    CHECKERS[manager] = checker
    APPLIERS[manager] = applier


def get_checker(manager: str) -> Checker | None:
    return CHECKERS.get(manager)


def check_workspace(ws: Workspace) -> WorkspaceResult:
    result = WorkspaceResult(workspace=ws)
    try:
        checker = CHECKERS.get(ws.manager)
        if not checker:
            result.status = "skip"
            result.error = f"unsupported manager {ws.manager}"
            return result
        out = checker(ws)
        if isinstance(out, tuple):
            result.changes, result.overrides_audit = out
        else:
            result.changes = out
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
