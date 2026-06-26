"""Workspace processing orchestration for plan/apply."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Literal

import repo_ops_upgrade_remediations_go  # noqa: F401 — side-effect: registers Go hooks
import repo_ops_upgrade_remediations_js  # noqa: F401 — side-effect: registers JS hooks
import repo_ops_upgrade_remediations_py  # noqa: F401 — side-effect: registers Python hooks
import repo_ops_upgrade_remediations_rust  # noqa: F401 — side-effect: registers Rust hooks
from repo_ops_core.filesystem import should_exclude
from repo_ops_core.git import is_git_dirty
from repo_ops_core.state import cache_clear_dirty_skip, cache_record_dirty_skip
from repo_ops_js.overrides import overrides_audit
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    snapshot_workspace_files,
)
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade_remediations_core.remediations import (
    dispatch as _dispatch_remediations,
)

from repo_ops_upgrade import registry as ecosystems
from repo_ops_upgrade import verify as verify_mod
from repo_ops_upgrade.post_apply import (
    classify_verify_fail_disposition,
    collect_changes_after_apply,
    revert_workspace_after_verify_fail,
)

logger = logging.getLogger(__name__)


class _RemediationsProxy:
    """Backward-compat shim so tests can patch ``orchestration.remediations.run_remediations``.

    Pre-existing tests use ``patch("repo_ops_upgrade.orchestration.remediations.run_remediations")``
    to suppress remediation side-effects during integration tests.  This proxy object
    is a drop-in attribute replacement for the old ``from ... import remediations``
    module reference while routing live calls through the global multi-ecosystem dispatch.
    """

    def run_remediations(
        self,
        result: WorkspaceResult,
        *,
        apply: bool,
        skip: set[str],
    ) -> None:
        _dispatch_remediations(result, apply=apply, skip=skip)


# Module-level ``remediations`` attribute: existing tests patch
# ``orchestration.remediations.run_remediations`` via this name.
remediations = _RemediationsProxy()

FAIL_FAST_EVENT = threading.Event()
OnVerifyFail = Literal["keep", "revert"]

# Protects stderr progress lines so parallel workers never interleave.
_PROGRESS_LOCK = threading.Lock()


def _emit_progress(result: WorkspaceResult, *, elapsed_s: float) -> None:
    """Emit one [done] line to stderr if progress is enabled.

    Suppressed by ``REPO_OPS_NO_PROGRESS=1`` or when the ``quiet`` parameter
    is passed to :func:`process_workspace`.  The lock prevents torn lines under
    parallel execution.
    """
    if os.environ.get("REPO_OPS_NO_PROGRESS") == "1":
        return
    ws = result.workspace
    label = ws.label or str(ws.path)
    n = len(result.changes)
    line = f"[done] {label} ({ws.manager}) — {n} change(s)"
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
    verify_test: bool,
    skip_remediations: set[str],
    allow_dirty: bool,
    on_verify_fail: OnVerifyFail = "keep",
    quiet: bool = False,
) -> WorkspaceResult:
    _t0 = time.monotonic()
    out = _process_workspace_body(
        result,
        do_apply=do_apply,
        do_verify=do_verify,
        verify_test=verify_test,
        skip_remediations=skip_remediations,
        allow_dirty=allow_dirty,
        on_verify_fail=on_verify_fail,
    )
    elapsed = time.monotonic() - _t0
    out.elapsed_s = elapsed
    if not quiet:
        _emit_progress(out, elapsed_s=elapsed)
    return out


def _process_workspace_body(
    result: WorkspaceResult,
    *,
    do_apply: bool,
    do_verify: bool,
    verify_test: bool,
    skip_remediations: set[str],
    allow_dirty: bool,
    on_verify_fail: OnVerifyFail,
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
            cache_record_dirty_skip(ws.git_repo, date.today())
            return result
        # Repo processed cleanly — clear any stale dirty-skip entry.
        cache_clear_dirty_skip(ws.git_repo)
        before_snap = snapshot_workspace_files(ws.path, ws.manager)
        try:
            behind_after = ecosystems.apply_workspace(ws)
            if behind_after:
                # Direct deps a latest-mode apply left behind (yarn, issue #110).
                result.behind_latest = behind_after
            after_snap = snapshot_workspace_files(ws.path, ws.manager)
            result.changes = diff_workspace_snapshots(
                before_snap, after_snap, ws.manager
            )
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
        remediations.run_remediations(result, apply=do_apply, skip=skip_remediations)

    if do_apply and do_verify and result.status != "fail":
        verr = verify_mod.verify_workspace(ws, verify_test=verify_test)
        if verr:
            result.verify_error = verr
            result.status = "fail"
            if before_snap is not None:
                reverted_ok = True
                if on_verify_fail == "revert":
                    try:
                        reverted_ok = revert_workspace_after_verify_fail(
                            ws, before_snap
                        )
                    except (RuntimeError, OSError, ValueError) as exc:
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
