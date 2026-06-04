"""Ecosystem checker/applier registry (populated by meta _register)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from repo_ops_js.overrides import overrides_audit
from repo_ops_js.workspaces import corepack_prepare
from repo_ops_models import Change, Workspace, WorkspaceResult

logger = logging.getLogger(__name__)

# A checker may return:
#   list[Change]                                  — changes only
#   tuple[list[Change], list[dict[str, str]]]     — changes + overrides_audit
#   tuple[list[Change], list[Change]]             — changes + behind_latest
#
# The two tuple forms are distinguished at dispatch time by inspecting the
# first element of the second list: dict → overrides_audit, Change → behind_latest.
Checker = Callable[
    [Workspace],
    list[Change]
    | tuple[list[Change], list[dict[str, str]]]
    | tuple[list[Change], list[Change]],
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
            changes, second = out
            result.changes = changes
            # Distinguish overrides_audit (list[dict]) from behind_latest (list[Change]).
            # An empty second list is treated as overrides_audit (existing behaviour).
            if second and isinstance(second[0], Change):
                result.behind_latest = [c for c in second if isinstance(c, Change)]
                if ws.ecosystem == "node":
                    result.overrides_audit = overrides_audit(ws.path, ws.manager)
            else:
                result.overrides_audit = [e for e in second if isinstance(e, dict)]
        else:
            result.changes = out
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "checker failed for %s at %s: %s",
            ws.manager,
            ws.path,
            exc,
            exc_info=True,
        )
        result.status = "fail"
        result.error = str(exc) or type(exc).__name__
    except Exception as exc:
        logger.exception(
            "unexpected checker error for %s at %s",
            ws.manager,
            ws.path,
        )
        result.status = "fail"
        result.error = str(exc) or type(exc).__name__
    return result


def apply_workspace(ws: Workspace) -> None:
    if ws.ecosystem == "node":
        corepack_prepare(ws.manager, ws.path)
    applier = APPLIERS.get(ws.manager)
    if not applier:
        raise RuntimeError(f"no applier for {ws.manager}")
    applier(ws)
