"""Workspace processing orchestration for plan/apply."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Literal

from repo_ops_core.filesystem import should_exclude
from repo_ops_core.git import is_git_dirty
from repo_ops_core.state import (
    cache_clear_dirty_skip,
    cache_record_behind_latest,
    cache_record_dirty_skip,
)
from repo_ops_js.overrides import overrides_audit
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    snapshot_workspace_files,
)
from repo_ops_models import DIRTY_SKIP_MARKER, Workspace, WorkspaceResult
from repo_ops_upgrade import registry as ecosystems
from repo_ops_upgrade import verify as verify_mod
from repo_ops_upgrade.post_apply import (
    capture_worktree_state,
    classify_verify_fail_disposition,
    collect_changes_after_apply,
    revert_workspace_after_verify_fail,
)
from repo_ops_upgrade_remediations_core.remediations import dispatch as dispatch_remediations
from repo_ops_upgrade_remediations_core.remediations import load_ecosystem_hooks

load_ecosystem_hooks()

logger = logging.getLogger(__name__)


OnVerifyFail = Literal["keep", "revert"]

# Protects stderr progress lines so parallel workers never interleave.
_PROGRESS_LOCK = threading.Lock()


def _format_elapsed(elapsed_s: float) -> str:
    """Human-readable duration for progress lines (glanceable slow-repo spotting)."""
    if elapsed_s < 60:
        return f"{elapsed_s:.1f}s"
    minutes = int(elapsed_s // 60)
    seconds = elapsed_s % 60
    return f"{minutes}m {seconds:.0f}s"


def _emit_progress(result: WorkspaceResult, *, elapsed_s: float) -> None:
    """Emit one [done] line to stderr if progress is enabled.

    The lock prevents torn lines under parallel execution.
    """
    ws = result.workspace
    label = ws.label or str(ws.path)
    n = len(result.changes)
    duration = _format_elapsed(elapsed_s)
    line = f"[done] {label} ({ws.manager}) — {n} change(s) in {duration}"
    if result.status == "fail":
        first_err = (result.verify_error or result.error or "failed").splitlines()[0]
        line = f"{line}, FAIL: {first_err}"
    with _PROGRESS_LOCK:
        print(line, file=sys.stderr)


def process_workspace(
    result: WorkspaceResult,
    *,
    do_apply: bool,
    do_verify: bool,
    allow_dirty: bool,
    on_verify_fail: OnVerifyFail = "keep",
    repo_started_dirty: bool | None = None,
) -> WorkspaceResult:
    _t0 = time.monotonic()
    out = _process_workspace_body(
        result,
        do_apply=do_apply,
        do_verify=do_verify,
        allow_dirty=allow_dirty,
        on_verify_fail=on_verify_fail,
        repo_started_dirty=repo_started_dirty,
    )
    elapsed = time.monotonic() - _t0
    out.elapsed_s = elapsed
    if out.status == "ok":
        # Feed the staleness store (plan and apply both compute behind_latest).
        # Skipped/failed workspaces carry no fresh behind data, so recording
        # only ok results keeps a dirty-skipped repo from spuriously resetting
        # its first-behind aging clock.
        cache_record_behind_latest(
            out.workspace.git_repo,
            out.workspace.manager,
            len(out.behind_latest),
            date.today(),
        )
    _emit_progress(out, elapsed_s=elapsed)
    return out


def _process_workspace_body(
    result: WorkspaceResult,
    *,
    do_apply: bool,
    do_verify: bool,
    allow_dirty: bool,
    on_verify_fail: OnVerifyFail,
    repo_started_dirty: bool | None = None,
) -> WorkspaceResult:
    ws = result.workspace
    if should_exclude(str(ws.path)):
        result.status = "skip"
        result.error = "excluded path"
        return result

    before_snap: dict[Path, bytes] | None = None
    before_tree = None
    if do_apply:
        # Snapshotted once per bundle by the caller when repo_started_dirty is
        # given, rather than re-checked live here — an earlier sibling workspace
        # in the same repo/run may have already applied its own (not-yet-
        # committed) changes, which would otherwise make every later sibling look
        # dirty and skip outright even though the repo was clean at run-start.
        # Falls back to a live check when the caller has no bundle context
        # (e.g. direct process_workspace callers/tests).
        result.started_dirty = (
            is_git_dirty(ws.git_repo) if repo_started_dirty is None else repo_started_dirty
        )
        if result.started_dirty and not allow_dirty:
            result.status = "skip"
            result.error = f"{DIRTY_SKIP_MARKER} (name the checkout as a root to update it anyway)"
            cache_record_dirty_skip(ws.git_repo, date.today())
            return result
        # Repo processed cleanly — clear any stale dirty-skip entry.
        cache_clear_dirty_skip(ws.git_repo)
        before_tree = capture_worktree_state(ws.git_repo)
        before_snap = snapshot_workspace_files(ws.path, ws.manager)
        try:
            behind_after = ecosystems.apply_workspace(ws)
            if behind_after:
                # Direct deps the apply left behind (yarn).
                result.behind_latest = behind_after
            after_snap = snapshot_workspace_files(ws.path, ws.manager)
            result.changes = diff_workspace_snapshots(before_snap, after_snap, ws.manager)
            if not result.changes:
                result.changes = collect_changes_after_apply(ws)
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            result.status = "fail"
            result.error = str(exc)
            return result
    else:
        result = ecosystems.check_workspace(ws)

    if not (do_apply and result.status == "fail"):
        result.pre_apply_snapshot = before_snap or {}
        dispatch_remediations(result, apply=do_apply)
        result.pre_apply_snapshot = {}

    # Verify exists to catch an upgrade this run made. When the apply and its
    # remediations wrote nothing, the gate can only be judging state the run
    # never touched, which is CI's job and costs a full gate per repo.
    wrote_nothing = (
        do_apply and before_tree is not None and capture_worktree_state(ws.git_repo) == before_tree
    )
    if wrote_nothing:
        result.verify_skipped = True
    if do_apply and do_verify and result.status != "fail" and not wrote_nothing:
        try:
            verr = verify_mod.verify_workspace(ws)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            # A verify that cannot finish (e.g. a gate timeout) is a failed
            # verify, and must reach the revert below like any other.
            verr = str(exc)
        if verr:
            result.verify_error = verr
            result.status = "fail"
            if before_snap is not None:
                reverted_ok = True
                if on_verify_fail == "revert":
                    try:
                        reverted_ok = revert_workspace_after_verify_fail(
                            ws, before_snap, before_tree
                        )
                    except (
                        RuntimeError,
                        OSError,
                        ValueError,
                        subprocess.SubprocessError,
                    ) as exc:
                        # Align with apply-path catch set so git timeouts
                        # classify as revert failure rather than worker crash.
                        logger.warning("revert after verify fail failed: %s", exc)
                        reverted_ok = False
                        prefix = result.error or ""
                        result.error = (
                            f"{prefix}; revert failed: {exc}".strip("; ")
                            if prefix
                            else f"revert failed: {exc}"
                        )
                    result.reverted_ok = reverted_ok
                result.verify_fail_disposition = classify_verify_fail_disposition(
                    ws, before_snap, reverted_ok=reverted_ok
                )

    return result


def filter_workspaces(
    workspaces: list[Workspace],
    eco_filter: str | None,
    manager_filter: str | None = None,
) -> list[Workspace]:
    result = workspaces
    if eco_filter:
        result = [w for w in result if w.ecosystem == eco_filter]
    if manager_filter:
        result = [w for w in result if w.manager == manager_filter]
    return result
