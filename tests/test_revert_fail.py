"""Regression tests for --on-verify-fail=revert silent-failure fixes (#26).

Covers:
  (a) A failed git checkout during revert results in reverted_ok=False and the
      disposition is NOT FAILED_CLEAN.
  (b) Apply-created untracked files are removed by git clean during revert.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_ops_models import Workspace
from repo_ops_upgrade.post_apply import (
    FAILED_CLEAN,
    FAILED_WITH_CHANGES,
    classify_verify_fail_disposition,
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


# ---------------------------------------------------------------------------
# (a) git checkout failure → reverted_ok=False, disposition != FAILED_CLEAN
# ---------------------------------------------------------------------------


def test_checkout_failure_returns_reverted_ok_false(tmp_path: Path, init_git_repo) -> None:
    """A non-zero git checkout during revert must return False."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)
    before_snap: dict[Path, bytes] = {}

    def _fake_run(cmd: list[str], cwd: Path, **kwargs: object) -> CompletedProcess[str]:
        if cmd[:2] == ["git", "diff"]:
            # Pretend there are tracked-file changes to check out.
            return CompletedProcess(cmd, 0, stdout="package-lock.json\n", stderr="")
        if cmd[:2] == ["git", "checkout"]:
            # Simulate checkout failure (e.g. conflicts / detached HEAD).
            return CompletedProcess(cmd, 1, stdout="", stderr="error: conflict")
        if cmd[:2] == ["git", "clean"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        result = revert_workspace_after_verify_fail(ws, before_snap)

    assert result is False


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

    def _fake_run(cmd: list[str], cwd: Path, **kwargs: object) -> CompletedProcess[str]:
        if cmd[:2] == ["git", "diff"]:
            return CompletedProcess(cmd, 0, stdout="package-lock.json\n", stderr="")
        if cmd[:2] == ["git", "checkout"]:
            return CompletedProcess(cmd, 1, stdout="", stderr="error: conflict")
        if cmd[:2] == ["git", "clean"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        # workspace_has_apply_artifacts porcelain call → empty (workspace looks clean)
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)
        disposition = classify_verify_fail_disposition(
            ws, before_snap, reverted_ok=reverted_ok
        )

    assert reverted_ok is False
    assert disposition == FAILED_WITH_CHANGES


# ---------------------------------------------------------------------------
# (b) Untracked apply-created files are removed during revert
# ---------------------------------------------------------------------------


def test_revert_removes_untracked_apply_files(tmp_path: Path, init_git_repo) -> None:
    """git clean must remove untracked files created by apply under the workspace."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)

    # Capture the before-snapshot (no lockfile changes expected here).
    lock = tmp_path / "package-lock.json"
    before_snap = {lock: lock.read_bytes()}

    # Simulate apply creating a new untracked file (e.g. a dist artifact or second lockfile).
    new_file = tmp_path / "yarn.lock"
    new_file.write_text("# apply created this\n", encoding="utf-8")
    assert new_file.exists()

    # Run the actual revert against the real git repo (no untracked-file changes tracked).
    reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)

    assert reverted_ok is True
    assert not new_file.exists(), (
        "git clean should have removed the untracked apply artifact"
    )


def test_revert_ok_succeeds_when_nothing_to_revert(tmp_path: Path, init_git_repo) -> None:
    """When no tracked or untracked changes exist, revert returns True (trap A)."""
    ws = _make_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    before_snap = {lock: lock.read_bytes()}

    # No mutations — workspace is clean.
    reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)

    assert reverted_ok is True
