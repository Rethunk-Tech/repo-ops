"""Data-loss regression tests for --on-verify-fail=revert path scoping.

Proves that revert_workspace_after_verify_fail at repo root:
  (a) Restores apply artifacts (lockfile changes reverted to pre-apply content).
  (b) Does NOT delete a pre-existing untracked file at repo root.
  (c) Does NOT discard a pre-existing dirty tracked edit outside the lockfile set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_ops_lockfiles.snapshots import snapshot_workspace_files
from repo_ops_models import Workspace
from repo_ops_upgrade.post_apply import (
    capture_worktree_state,
    revert_workspace_after_verify_fail,
)

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _commit_all(path: Path, msg: str = "init") -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, check=True, capture_output=True)


def _root_npm_workspace(tmp_path: Path, init_git_repo) -> Workspace:
    """Git repo at root; npm workspace; commits package-lock.json."""
    init_git_repo(tmp_path)
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":1}', encoding="utf-8")
    _commit_all(tmp_path)
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="root-npm",
    )


# ---------------------------------------------------------------------------
# (a) Revert restores apply artifacts
# ---------------------------------------------------------------------------


def test_revert_restores_modified_lockfile(tmp_path: Path, init_git_repo) -> None:
    """Tracked lockfile modified by apply (on disk, not staged) is restored.

    apply mutates files on disk without staging them.  restore_workspace_snapshot
    handles the content restore from before_snap; the worktree state restore
    covers files outside the snapshot scope (e.g. pyproject.toml).
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    original = lock.read_bytes()

    # Capture before-state then simulate apply mutating the lockfile on disk.
    # Do NOT stage: apply never stages.
    before_snap = snapshot_workspace_files(ws.path, ws.manager)
    before_tree = capture_worktree_state(ws.git_repo)
    lock.write_text('{"lockfileVersion":2,"mutated":true}', encoding="utf-8")

    reverted_ok = revert_workspace_after_verify_fail(ws, before_snap, before_tree)

    assert reverted_ok is True
    assert lock.read_bytes() == original, "revert must restore lockfile to pre-apply content"


# ---------------------------------------------------------------------------
# (b) Pre-existing untracked file at repo root is never deleted
# ---------------------------------------------------------------------------


def test_revert_preserves_preexisting_untracked_file(tmp_path: Path, init_git_repo) -> None:
    """An operator's pre-existing untracked file is not deleted by revert.

    Real-git test: notes.txt is untracked before apply runs.
    After revert the file must still exist on disk.
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)

    # Operator already has this file; it is NOT a lockfile or manifest.
    notes = tmp_path / "notes.txt"
    notes.write_text("operator notes\n", encoding="utf-8")

    lock = tmp_path / "package-lock.json"
    before_snap = snapshot_workspace_files(ws.path, ws.manager)
    before_tree = capture_worktree_state(ws.git_repo)

    # Simulate apply: mutate the lockfile.
    lock.write_text('{"lockfileVersion":2}', encoding="utf-8")

    revert_workspace_after_verify_fail(ws, before_snap, before_tree)

    assert notes.exists(), "revert must not delete a pre-existing untracked file (notes.txt)"


# ---------------------------------------------------------------------------
# (c) Pre-existing dirty tracked edit is never discarded
# ---------------------------------------------------------------------------


def test_revert_preserves_preexisting_dirty_tracked_edit(tmp_path: Path, init_git_repo) -> None:
    """A dirty tracked edit unrelated to apply is not reverted.

    Real-git test: app.py is modified by the operator BEFORE apply runs.
    After revert the operator's edit must still be present on disk.
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)

    # Commit a non-lockfile source file.
    app_py = tmp_path / "app.py"
    app_py.write_text("# version 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add app.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    lock = tmp_path / "package-lock.json"
    before_snap = snapshot_workspace_files(ws.path, ws.manager)

    # Operator's pre-existing dirty edit, made before apply.
    app_py.write_text("# version 2 (operator edit)\n", encoding="utf-8")
    dirty_content = app_py.read_bytes()
    before_tree = capture_worktree_state(ws.git_repo)

    # Simulate apply mutating the lockfile.
    lock.write_text('{"lockfileVersion":2}', encoding="utf-8")

    revert_workspace_after_verify_fail(ws, before_snap, before_tree)

    assert app_py.read_bytes() == dirty_content, (
        "revert must not discard a pre-existing dirty tracked edit (app.py)"
    )


# ---------------------------------------------------------------------------
# (d) Pre-existing dirty tracked artifact-basename file apply never touched
#     is NOT git-checkout-reverted; apply-modified lockfile IS restored
# ---------------------------------------------------------------------------


def test_revert_preserves_preexisting_dirty_artifact_not_in_snapshot(
    tmp_path: Path,
    init_git_repo,
) -> None:
    """A dirty tracked file with artifact basename that apply never touched survives revert.

    Real-git test: subdir/requirements.txt is committed and the operator dirtied
    it before apply ran.  apply only modifies package-lock.json (the npm lockfile
    captured in before_snap).  After revert:
      - subdir/requirements.txt still holds the operator's dirty content.
      - package-lock.json is restored to its pre-apply state.

    Guards against reverting a pre-existing dirty tracked file only because its
    basename matches a lockfile name.
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)

    # Commit subdir/requirements.txt — tracked, artifact basename, but NOT an npm
    # lockfile so snapshot_workspace_files(npm) will not capture it.
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    req = subdir / "requirements.txt"
    req.write_text("flask==2.0.0\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "subdir/requirements.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add subdir/requirements.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    # Capture pre-apply snapshot (npm lockfiles only — requirements.txt not included).
    before_snap = snapshot_workspace_files(ws.path, ws.manager)

    # Operator dirty-edits requirements.txt before apply.
    req.write_text("flask==2.0.0\nrequests==2.28.0\n", encoding="utf-8")
    dirty_content = req.read_bytes()
    before_tree = capture_worktree_state(ws.git_repo)

    # Simulate apply modifying only the lockfile.
    lock = tmp_path / "package-lock.json"
    original_lock = lock.read_bytes()
    lock.write_text('{"lockfileVersion":2,"mutated":true}', encoding="utf-8")

    reverted_ok = revert_workspace_after_verify_fail(ws, before_snap, before_tree)

    assert reverted_ok is True
    # Operator's pre-existing dirty edit must survive — apply never touched this file.
    assert req.read_bytes() == dirty_content, (
        "revert must not discard a pre-existing dirty tracked file whose basename is "
        "a lockfile name but that apply never touched (subdir/requirements.txt)"
    )
    # The lockfile apply DID modify must be restored (via snapshot restore path).
    assert lock.read_bytes() == original_lock, (
        "revert must restore the lockfile that apply modified (package-lock.json)"
    )


# ---------------------------------------------------------------------------
# Snapshot scope: pdm / pipenv / deno managers capture their lockfiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lockfile_name,manager",
    [
        ("pdm.lock", "pdm"),
        ("Pipfile.lock", "pipenv"),
        ("deno.lock", "deno"),
    ],
)
def test_snapshot_captures_manager_lock(tmp_path: Path, lockfile_name: str, manager: str) -> None:
    (tmp_path / lockfile_name).write_text("{}")
    snap = snapshot_workspace_files(tmp_path, manager)
    assert any(p.name == lockfile_name for p in snap)
