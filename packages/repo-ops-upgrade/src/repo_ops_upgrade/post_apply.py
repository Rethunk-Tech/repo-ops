"""Post-apply change detection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_core.version_utils import normalize_version
from repo_ops_lockfiles.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace

from repo_ops_upgrade.registry import Checker

logger = logging.getLogger(__name__)

FAILED_WITH_CHANGES = "FAILED_WITH_CHANGES"
FAILED_CLEAN = "FAILED_CLEAN"


def collect_changes_after_apply(
    ws: Workspace,
    *,
    checker: Checker | None = None,
) -> list[Change]:
    """Lightweight post-apply change detection for remediation context."""
    repo = ws.git_repo
    rel = ws.path
    try:
        rel = ws.path.relative_to(repo)
    except ValueError:
        rel = ws.path
    rel_s = str(rel)
    lock_names = (
        list(NODE_LOCKFILES)
        + list(PY_LOCKFILES)
        + list(GO_LOCKFILES)
        + list(RUST_LOCKFILES)
    )
    proc = run(
        ["git", "diff", "--name-only", "--", *lock_names],
        repo,
        timeout=60,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        changes: list[Change] = []
        for line in proc.stdout.splitlines():
            p = line.strip()
            if not p.startswith(rel_s):
                continue
            fname = Path(p).name
            if fname == "package.json":
                proc2 = run(
                    ["git", "diff", "--", p],
                    repo,
                    timeout=60,
                )
                if proc2.returncode == 0:
                    m = re.findall(
                        r'^\+\s*"([^"]+)":\s*"([^"]+)"',
                        proc2.stdout,
                        re.MULTILINE,
                    )
                    for name, ver in m:
                        if name in ("dependencies", "devDependencies"):
                            continue
                        changes.append(Change(name, "?", normalize_version(ver)))
            elif fname.endswith(".lock") or fname in (
                "go.sum",
                "go.mod",
                "requirements.txt",
            ):
                changes.append(Change(fname, "changed", "updated"))
        if changes:
            return changes
    if checker:
        try:
            out = checker(ws)
            if isinstance(out, tuple):
                return out[0]
            return out
        except (RuntimeError, OSError, ValueError) as exc:
            logger.debug("post-apply checker fallback failed for %s: %s", ws.path, exc)
    return []


def _workspace_git_pathspec(ws: Workspace) -> str:
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve())
    except ValueError:
        return "."
    return "." if rel == Path(".") else str(rel)


def workspace_has_apply_artifacts(
    ws: Workspace,
    before_snap: dict[Path, bytes],
) -> bool:
    """True when apply left lockfile/manifest or git changes under the workspace."""
    after_snap = snapshot_workspace_files(ws.path, ws.manager)
    if diff_workspace_snapshots(before_snap, after_snap, ws.manager):
        return True
    pathspec = _workspace_git_pathspec(ws)
    proc = run(
        ["git", "status", "--porcelain", "--", pathspec],
        ws.git_repo,
        timeout=60,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def classify_verify_fail_disposition(
    ws: Workspace,
    before_snap: dict[Path, bytes],
    *,
    reverted_ok: bool = True,
) -> str:
    if not reverted_ok:
        return FAILED_WITH_CHANGES
    if workspace_has_apply_artifacts(ws, before_snap):
        return FAILED_WITH_CHANGES
    return FAILED_CLEAN


def revert_workspace_after_verify_fail(
    ws: Workspace,
    before_snap: dict[Path, bytes],
) -> bool:
    """Restore pre-apply lockfile snapshot and git-tracked paths under the workspace.

    Returns True when all revert operations succeeded, False if any git operation
    reported a non-zero exit code (in which case the workspace may be partially
    reverted).
    """
    restore_workspace_snapshot(ws.path, before_snap, ws.manager)
    pathspec = _workspace_git_pathspec(ws)
    reverted_ok = True

    diff_proc = run(
        ["git", "diff", "--name-only", "--", pathspec],
        ws.git_repo,
        timeout=60,
    )
    if diff_proc.returncode == 0 and diff_proc.stdout.strip():
        paths = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]
        checkout_proc = run(
            ["git", "checkout", "--", *paths],
            ws.git_repo,
            timeout=120,
        )
        if checkout_proc.returncode != 0:
            logger.warning(
                "git checkout failed during revert for %s (rc=%d): %s",
                ws.path,
                checkout_proc.returncode,
                (checkout_proc.stderr or checkout_proc.stdout).strip(),
            )
            reverted_ok = False

    # Remove untracked files created by apply (e.g. new lockfiles) scoped to workspace.
    clean_proc = run(
        ["git", "clean", "-fd", "--", pathspec],
        ws.git_repo,
        timeout=60,
    )
    if clean_proc.returncode != 0:
        logger.warning(
            "git clean failed during revert for %s (rc=%d): %s",
            ws.path,
            clean_proc.returncode,
            (clean_proc.stderr or clean_proc.stdout).strip(),
        )
        reverted_ok = False

    return reverted_ok
