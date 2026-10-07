"""Revert after a failed verify must not fail silently.

Covers:
  (a) A failed git checkout during revert results in reverted_ok=False and the
      disposition is NOT FAILED_CLEAN.
  (b) Apply-created untracked files are removed during revert.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_ops_models import OwnedEdit, Workspace
from repo_ops_upgrade.post_apply import (
    FAILED_CLEAN,
    FAILED_WITH_CHANGES,
    capture_worktree_state,
    classify_verify_fail_disposition,
    record_owned_edits,
    revert_workspace_after_verify_fail,
)


def _make_workspace(tmp_path: Path, init_git_repo) -> Workspace:
    init_git_repo(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion":1}', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )


def _lock_edits(repo: Path) -> dict[str, OwnedEdit]:
    return {"package-lock.json": OwnedEdit(None, True, (repo / "package-lock.json").read_bytes())}


def _checkout_fails(cmd: list[str], cwd: Path, **kwargs: object) -> CompletedProcess[str]:
    if cmd[:2] == ["git", "ls-tree"]:
        return CompletedProcess(cmd, 0, stdout="package-lock.json\n", stderr="")
    if cmd[:2] == ["git", "checkout"]:
        return CompletedProcess(cmd, 1, stdout="", stderr="error: conflict")
    return CompletedProcess(cmd, 0, stdout="", stderr="")


def _revert(ws: Workspace, before_snap, before_tree) -> bool:
    """Revert as the pipeline does: record what apply wrote, then roll back."""
    edits = record_owned_edits(ws, before_snap, before_tree, capture_worktree_state(ws.git_repo))
    return revert_workspace_after_verify_fail(ws, edits, before_tree).ok


# ---------------------------------------------------------------------------
# (a) git checkout failure → reverted_ok=False, disposition != FAILED_CLEAN
# ---------------------------------------------------------------------------


def test_checkout_failure_returns_reverted_ok_false(tmp_path: Path, init_git_repo) -> None:
    """A non-zero git checkout during revert must return False."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_checkout_fails):
        result = revert_workspace_after_verify_fail(ws, _lock_edits(tmp_path), {})

    assert result.ok is False


def test_classify_uses_reverted_ok_flag(tmp_path: Path, init_git_repo) -> None:
    """classify_verify_fail_disposition must NOT return FAILED_CLEAN when reverted_ok=False."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)
    before_snap: dict[Path, bytes] = {}

    # Even if the workspace looks clean by artifact check, a failed revert must
    # yield FAILED_WITH_CHANGES.
    disposition = classify_verify_fail_disposition(ws, before_snap, reverted_ok=False)
    assert disposition == FAILED_WITH_CHANGES
    assert disposition != FAILED_CLEAN


def test_classify_failed_checkout_end_to_end(tmp_path: Path, init_git_repo) -> None:
    """Full path: failed checkout → reverted_ok False → disposition FAILED_WITH_CHANGES."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)
    before_snap: dict[Path, bytes] = {}

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_checkout_fails):
        reverted_ok = revert_workspace_after_verify_fail(ws, _lock_edits(tmp_path), {}).ok
        disposition = classify_verify_fail_disposition(ws, before_snap, reverted_ok=reverted_ok)

    assert reverted_ok is False
    assert disposition == FAILED_WITH_CHANGES


# ---------------------------------------------------------------------------
# (b) Untracked apply-created files are removed during revert
# ---------------------------------------------------------------------------


def test_revert_removes_untracked_apply_files(tmp_path: Path, init_git_repo) -> None:
    """Revert must remove untracked files created by apply."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)

    # Capture the before-snapshot (no lockfile changes expected here).
    lock = tmp_path / "package-lock.json"
    before_snap = {lock: lock.read_bytes()}
    before_tree = capture_worktree_state(ws.git_repo)

    # Simulate apply creating a new untracked file (e.g. a dist artifact or second lockfile).
    new_file = tmp_path / "yarn.lock"
    new_file.write_text("# apply created this\n", encoding="utf-8")
    assert new_file.exists()

    reverted_ok = _revert(ws, before_snap, before_tree)

    assert reverted_ok is True
    assert not new_file.exists(), "revert should have removed the untracked apply artifact"


def test_revert_ok_succeeds_when_nothing_to_revert(tmp_path: Path, init_git_repo) -> None:
    """When no tracked or untracked changes exist, revert returns True (trap A)."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    before_snap = {lock: lock.read_bytes()}

    # No mutations: workspace is clean.
    reverted_ok = _revert(ws, before_snap, capture_worktree_state(ws.git_repo))

    assert reverted_ok is True
