"""Workspace processing orchestration for plan/apply."""

from __future__ import annotations

import threading

from repo_ops_core.filesystem import should_exclude
from repo_ops_core.git import is_git_dirty
from repo_ops_js.overrides import overrides_audit
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    snapshot_workspace_files,
)
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade_remediations_js import remediations

from repo_ops_upgrade import registry as ecosystems
from repo_ops_upgrade import verify as verify_mod
from repo_ops_upgrade.post_apply import collect_changes_after_apply

FAIL_FAST_EVENT = threading.Event()


def process_workspace(
    result: WorkspaceResult,
    *,
    do_apply: bool,
    do_verify: bool,
    verify_test: bool,
    skip_remediations: set[str],
    allow_dirty: bool,
) -> WorkspaceResult:
    ws = result.workspace
    if FAIL_FAST_EVENT.is_set():
        result.status = "skip"
        result.error = "fail-fast"
        return result

    if should_exclude(str(ws.path)):
        result.status = "skip"
        result.error = "excluded path"
        return result

    if do_apply:
        if is_git_dirty(ws.git_repo) and not allow_dirty:
            result.status = "skip"
            result.error = "dirty git worktree (use --allow-dirty)"
            return result
        before_snap = snapshot_workspace_files(ws.path, ws.manager)
        try:
            ecosystems.apply_workspace(ws)
            after_snap = snapshot_workspace_files(ws.path, ws.manager)
            result.changes = diff_workspace_snapshots(
                before_snap, after_snap, ws.manager
            )
            if not result.changes:
                result.changes = collect_changes_after_apply(ws)
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
        except Exception as exc:
            result.status = "fail"
            result.error = str(exc)
            return result
    else:
        result = ecosystems.check_workspace(ws)

    remediations.run_remediations(result, apply=do_apply, skip=skip_remediations)

    if do_apply and do_verify and result.status != "fail":
        verr = verify_mod.verify_workspace(ws, verify_test=verify_test)
        if verr:
            result.verify_error = verr
            result.status = "fail"

    return result


def filter_workspaces(
    workspaces: list[Workspace],
    eco_filter: str | None,
) -> list[Workspace]:
    if not eco_filter:
        return workspaces
    return [w for w in workspaces if w.ecosystem == eco_filter]
