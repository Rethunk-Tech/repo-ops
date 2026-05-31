"""Workspace processing orchestration for plan/apply."""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Literal

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
from repo_ops_upgrade.post_apply import (
    classify_verify_fail_disposition,
    collect_changes_after_apply,
    revert_workspace_after_verify_fail,
)

logger = logging.getLogger(__name__)

FAIL_FAST_EVENT = threading.Event()
OnVerifyFail = Literal["keep", "revert"]


def process_workspace(
    result: WorkspaceResult,
    *,
    do_apply: bool,
    do_verify: bool,
    verify_test: bool,
    skip_remediations: set[str],
    allow_dirty: bool,
    on_verify_fail: OnVerifyFail = "keep",
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

    before_snap: dict[Path, bytes] | None = None
    if do_apply:
        result.started_dirty = is_git_dirty(ws.git_repo)
        if result.started_dirty and not allow_dirty:
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
                result.changes = collect_changes_after_apply(
                    ws,
                    checker=ecosystems.get_checker(ws.manager),
                )
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
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
            if before_snap is not None:
                if on_verify_fail == "revert":
                    try:
                        revert_workspace_after_verify_fail(ws, before_snap)
                    except (RuntimeError, OSError, ValueError) as exc:
                        logger.warning("revert after verify fail failed: %s", exc)
                        prefix = result.error or ""
                        result.error = (
                            f"{prefix}; revert failed: {exc}".strip("; ")
                            if prefix
                            else f"revert failed: {exc}"
                        )
                result.verify_fail_disposition = classify_verify_fail_disposition(
                    ws, before_snap
                )

    return result


def filter_workspaces(
    workspaces: list[Workspace],
    eco_filter: str | None,
) -> list[Workspace]:
    if not eco_filter:
        return workspaces
    return [w for w in workspaces if w.ecosystem == eco_filter]
