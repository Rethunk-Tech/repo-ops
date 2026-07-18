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

import repo_ops_upgrade_remediations_deno  # noqa: F401 — side-effect: registers Deno hooks
import repo_ops_upgrade_remediations_go  # noqa: F401 — side-effect: registers Go hooks
import repo_ops_upgrade_remediations_js  # noqa: F401 — side-effect: registers JS hooks
import repo_ops_upgrade_remediations_py  # noqa: F401 — side-effect: registers Python hooks
import repo_ops_upgrade_remediations_rust  # noqa: F401 — side-effect: registers Rust hooks
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


# Env gate for go generate on the production dispatch path (orchestration →
# remediations-core.dispatch).  Kept here because go.run_remediations is not
# on the apply path; do not weaken that helper's own effective_skip.
_GO_GENERATE_ENV = "REPO_OPS_REMEDIATE_GO_GENERATE"


class _RemediationsProxy:
    """Intentional test seam: tests patch ``orchestration.remediations.run_remediations``.

    # compat: patch site for test_progress_lines / test_staleness_state /
    # test_on_verify_fail — keep this proxy rather than migrating every call site.
    Routes live calls through remediations-core.dispatch while enforcing the
    go_generate env gate that go.run_remediations already applies.
    """

    def run_remediations(
        self,
        result: WorkspaceResult,
        *,
        apply: bool,
        skip: set[str],
    ) -> None:
        effective_skip = set(skip)
        # go_generate is default-off on the production dispatch path so
        # //go:generate never runs during fleet apply unless explicitly opted in.
        if os.environ.get(_GO_GENERATE_ENV, "") != "1":
            effective_skip.add("go_generate")
        _dispatch_remediations(result, apply=apply, skip=effective_skip)


# Module-level ``remediations`` attribute: existing tests patch
# ``orchestration.remediations.run_remediations`` via this name.
remediations = _RemediationsProxy()

FAIL_FAST_EVENT = threading.Event()
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

    Suppressed by ``REPO_OPS_NO_PROGRESS=1`` or when the ``quiet`` parameter
    is passed to :func:`process_workspace`.  The lock prevents torn lines under
    parallel execution.
    """
    if os.environ.get("REPO_OPS_NO_PROGRESS") == "1":
        return
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
            result.error = f"{DIRTY_SKIP_MARKER} (use --allow-dirty)"
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
