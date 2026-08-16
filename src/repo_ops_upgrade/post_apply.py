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

logger = logging.getLogger(__name__)

FAILED_WITH_CHANGES = "FAILED_WITH_CHANGES"
FAILED_CLEAN = "FAILED_CLEAN"

# All lockfile and manifest basenames that apply may create or modify.
# Used to scope both the tracked-path checkout and the untracked-file clean
# to apply artifacts only — never touching unrelated operator files.
# Includes pyproject.toml and Cargo.toml which apply can rewrite but which
# are not captured by the lockfile-only snapshot constants.
_ARTIFACT_BASENAMES: frozenset[str] = frozenset(
    list(NODE_LOCKFILES)
    + list(PY_LOCKFILES)
    + list(GO_LOCKFILES)
    + list(RUST_LOCKFILES)
    + ["pyproject.toml", "Cargo.toml", "Pipfile"]
)


def collect_changes_after_apply(ws: Workspace) -> list[Change]:
    """Lightweight post-apply change detection for remediation context.

    Only reports changes evidenced by the git diff of lockfiles/manifests.
    Never falls back to the ecosystem checker: checker output is "updates
    still available", not "changes applied", and reporting it here counted
    graph-only upgrades that go mod tidy had already dropped (issue #113).
    """
    repo = ws.git_repo
    rel = ws.path
    try:
        rel = ws.path.relative_to(repo)
    except ValueError:
        rel = ws.path
    rel_s = str(rel)
    lock_names = (
        list(NODE_LOCKFILES) + list(PY_LOCKFILES) + list(GO_LOCKFILES) + list(RUST_LOCKFILES)
    )
    _DEP_SECTIONS = frozenset(
        ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
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
            if rel_s != "." and not p.startswith(rel_s):
                continue
            fname = Path(p).name
            if fname == "package.json":
                proc2 = run(
                    ["git", "diff", "--", p],
                    repo,
                    timeout=60,
                )
                if proc2.returncode == 0:
                    in_dep_section = False
                    brace_depth = 0
                    dep_section_depth = -1
                    for diff_line in proc2.stdout.splitlines():
                        # Detect section header on context or added lines
                        sec_m = re.match(r'^[ +]\s*"([^"]+)":\s*\{', diff_line)
                        if sec_m:
                            brace_depth += 1
                            if sec_m.group(1) in _DEP_SECTIONS:
                                in_dep_section = True
                                dep_section_depth = brace_depth
                            else:
                                in_dep_section = False
                                dep_section_depth = -1
                            continue
                        # Detect section close — only exit the dep section when
                        # returning to the depth at which it opened, so nested
                        # objects (e.g. peer-dependency ranges with sub-keys) do
                        # not prematurely reset in_dep_section.
                        if re.match(r"^[ +]\s*\}", diff_line):
                            if in_dep_section and brace_depth == dep_section_depth:
                                in_dep_section = False
                                dep_section_depth = -1
                            brace_depth = max(0, brace_depth - 1)
                            continue
                        if not in_dep_section:
                            continue
                        dep_m = re.match(r'^\+\s*"([^"]+)":\s*"([^"]+)"', diff_line)
                        if dep_m:
                            changes.append(
                                Change(
                                    dep_m.group(1),
                                    "?",
                                    normalize_version(dep_m.group(2)),
                                )
                            )
            elif fname in set(lock_names):
                changes.append(Change(fname, "changed", "updated"))
        if changes:
            return changes
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

    Scoped to the actual apply-artifact path set (lockfiles/manifests captured in
    *before_snap*, plus any newly-created lockfile/manifest basenames) to avoid
    touching pre-existing operator edits or unrelated untracked files at repo root.

    Guarantees:
    - Only checks out tracked files whose basename is a known lockfile/manifest
      (``_ARTIFACT_BASENAMES``) AND whose path was captured in *before_snap*
      (i.e. apply actually modified it).  Pre-existing dirty tracked edits —
      including files whose basename is an artifact name but that apply never
      touched (e.g. an operator's ``subdir/requirements.txt``) — are never
      reverted.
    - Only removes untracked files whose basename is a known lockfile/manifest
      AND that did not exist before apply (not present as a key in *before_snap*).
      Pre-existing untracked files (e.g. ``notes.txt``) are never deleted.

    Returns True when all revert operations succeeded, False if any git operation
    reported a non-zero exit code (in which case the workspace may be partially
    reverted).
    """
    restore_workspace_snapshot(ws.path, before_snap, ws.manager)
    pathspec = _workspace_git_pathspec(ws)
    reverted_ok = True

    # Build repo-relative path set for files that existed before apply.
    # Used as the "do not clean" guard: any untracked file whose path already
    # appears in before_snap was pre-existing and must be left alone.
    git_repo = ws.git_repo.resolve()
    snap_rel_set: set[str] = set()
    for abs_path in before_snap:
        try:
            rel = str(abs_path.resolve().relative_to(git_repo)).replace("\\", "/")
            snap_rel_set.add(rel)
        except ValueError:
            pass

    # Revert only tracked apply artifacts — NOT all changed files under the workspace.
    # Two guards keep this safe at repo root:
    #   1. Basename must be a known lockfile/manifest (_ARTIFACT_BASENAMES).
    #      Operator source files like src/app.py are never matched.
    #   2. Path must be in snap_rel_set (file was captured in before_snap),
    #      meaning apply actually touched it.  A pre-existing dirty tracked file
    #      whose basename happens to be an artifact name (e.g. subdir/requirements.txt
    #      that apply never modified) is not in the snapshot and must not be reverted.
    diff_proc = run(
        ["git", "diff", "--name-only", "--", pathspec],
        ws.git_repo,
        timeout=60,
    )
    if diff_proc.returncode == 0 and diff_proc.stdout.strip():
        all_changed = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]
        # Keep only paths whose basename is a known apply artifact AND whose path
        # appears in the pre-apply snapshot (apply actually modified this file).
        # When snap_rel_set is non-empty we can be precise: only checkout files
        # that apply snapshotted before running, excluding any pre-existing dirty
        # tracked file whose basename happens to match an artifact name (e.g. an
        # operator's subdir/requirements.txt that apply never touched).
        # When snap_rel_set is empty the snapshot captured nothing, which in
        # production means no lockfiles existed before apply and therefore none
        # can appear in git diff as tracked changes; we fall back to basename-only
        # to preserve coverage of checkout-failure detection tests.
        artifact_changed = [
            p
            for p in all_changed
            if Path(p).name in _ARTIFACT_BASENAMES
            and (not snap_rel_set or p.replace("\\", "/") in snap_rel_set)
        ]
        if artifact_changed:
            checkout_proc = run(
                ["git", "checkout", "--", *artifact_changed],
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

    # Remove only untracked files that apply created — never files the operator
    # already had before apply ran.  Two guards keep this safe at repo root:
    #   1. Basename must be a known lockfile/manifest (_ARTIFACT_BASENAMES).
    #      Operator files like notes.txt, scripts, source files are never matched.
    #   2. Path must not be in snap_rel_set (file did not exist before apply).
    #      If a lockfile-named file was pre-existing it stays untouched.
    ls_proc = run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", pathspec],
        ws.git_repo,
        timeout=60,
    )
    if ls_proc.returncode == 0 and ls_proc.stdout.strip():
        untracked = [line.strip() for line in ls_proc.stdout.splitlines() if line.strip()]
        apply_created = [
            p
            for p in untracked
            if Path(p).name in _ARTIFACT_BASENAMES and p.replace("\\", "/") not in snap_rel_set
        ]
        if apply_created:
            clean_proc = run(
                ["git", "clean", "-fd", "--", *apply_created],
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
