"""Data-loss regression tests for --on-verify-fail=revert path scoping.

Proves that revert_workspace_after_verify_fail at repo root:
  (a) Restores apply artifacts (lockfile changes reverted to pre-apply content).
  (b) Does NOT delete a pre-existing untracked file at repo root.
  (c) Does NOT discard a pre-existing dirty tracked edit outside the lockfile set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_ops_lockfiles.snapshots import snapshot_workspace_files
from repo_ops_models import Workspace
from repo_ops_upgrade.post_apply import revert_workspace_after_verify_fail

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _commit_all(path: Path, msg: str = "init") -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=path, check=True, capture_output=True
    )


def _root_npm_workspace(tmp_path: Path, init_git_repo) -> Workspace:
    """Git repo at root; npm workspace; commits package-lock.json."""
    init_git_repo(tmp_path)
    (tmp_path / "package-lock.json").write_text(
        '{"lockfileVersion":1}', encoding="utf-8"
    )
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
    handles the content restore from before_snap; the git checkout fallback
    covers files not in the snapshot scope (e.g. pyproject.toml).
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    original = lock.read_bytes()

    # Capture before-state then simulate apply mutating the lockfile on disk.
    # Do NOT stage — apply never stages; git checkout restores from HEAD.
    before_snap = snapshot_workspace_files(ws.path, ws.manager)
    lock.write_text('{"lockfileVersion":2,"mutated":true}', encoding="utf-8")

    reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)

    assert reverted_ok is True
    assert lock.read_bytes() == original, (
        "revert must restore lockfile to pre-apply content"
    )


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

    # Simulate apply: mutate the lockfile.
    lock.write_text('{"lockfileVersion":2}', encoding="utf-8")

    revert_workspace_after_verify_fail(ws, before_snap)

    assert notes.exists(), (
        "revert must not delete a pre-existing untracked file (notes.txt)"
    )


def test_revert_clean_skips_non_artifact_via_mock(tmp_path: Path, init_git_repo) -> None:
    """git clean is called with only apply-created lockfile names, not notes.txt.

    Mock test: ls-files returns [notes.txt, yarn.lock].
    git clean must be invoked once and must not include notes.txt.
    yarn.lock is a known artifact name absent from before_snap → eligible.
    notes.txt is not a known artifact basename → must be excluded.
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    before_snap = {lock: lock.read_bytes()}

    clean_calls: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: Path, **kwargs: object) -> CompletedProcess[str]:
        if cmd[:2] == ["git", "diff"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "ls-files"]:
            # Pretend both a pre-existing operator file and an apply-created lockfile
            # are untracked.
            return CompletedProcess(cmd, 0, stdout="notes.txt\nyarn.lock\n", stderr="")
        if cmd[:2] == ["git", "clean"]:
            clean_calls.append(list(cmd))
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)

    assert reverted_ok is True
    # git clean must have been called (yarn.lock is an apply artifact).
    assert clean_calls, "expected git clean to be invoked for apply-created yarn.lock"
    assert len(clean_calls) == 1
    # notes.txt must not be in the clean invocation.
    assert "notes.txt" not in clean_calls[0], (
        "git clean must not include pre-existing non-artifact file notes.txt"
    )
    # yarn.lock (apply-created artifact) must be cleaned.
    assert "yarn.lock" in clean_calls[0], (
        "git clean must include the apply-created yarn.lock"
    )


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
    subprocess.run(
        ["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "add app.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    lock = tmp_path / "package-lock.json"
    before_snap = snapshot_workspace_files(ws.path, ws.manager)

    # Operator's pre-existing dirty edit (done before apply, with --allow-dirty).
    app_py.write_text("# version 2 (operator edit)\n", encoding="utf-8")
    dirty_content = app_py.read_bytes()

    # Simulate apply mutating the lockfile.
    lock.write_text('{"lockfileVersion":2}', encoding="utf-8")

    revert_workspace_after_verify_fail(ws, before_snap)

    assert app_py.read_bytes() == dirty_content, (
        "revert must not discard a pre-existing dirty tracked edit (app.py)"
    )


def test_revert_checkout_skips_non_artifact_via_mock(tmp_path: Path, init_git_repo) -> None:
    """git checkout is called with only artifact paths, not app.py.

    Mock test: git diff returns [package-lock.json, app.py].
    checkout must be invoked with only package-lock.json.
    """
    ws = _root_npm_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    before_snap = {lock: lock.read_bytes()}

    checkout_calls: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: Path, **kwargs: object) -> CompletedProcess[str]:
        if cmd[:2] == ["git", "diff"]:
            # diff shows both lockfile and operator's source file as changed.
            return CompletedProcess(
                cmd, 0, stdout="package-lock.json\napp.py\n", stderr=""
            )
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(list(cmd))
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "ls-files"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)

    assert reverted_ok is True
    assert checkout_calls, "expected git checkout to be invoked for package-lock.json"
    assert len(checkout_calls) == 1
    checkout_args = checkout_calls[0]
    # app.py must not be in the checkout invocation.
    assert "app.py" not in checkout_args, (
        "git checkout must not include the operator's non-artifact dirty file app.py"
    )
    # package-lock.json (apply artifact) must be checked out.
    assert "package-lock.json" in checkout_args, (
        "git checkout must include the apply-modified package-lock.json"
    )


# ---------------------------------------------------------------------------
# (d) Pre-existing dirty tracked artifact-basename file apply never touched
#     is NOT git-checkout-reverted; apply-modified lockfile IS restored
# ---------------------------------------------------------------------------


def test_revert_preserves_preexisting_dirty_artifact_not_in_snapshot(
    tmp_path: Path, init_git_repo,
) -> None:
    """A dirty tracked file with artifact basename that apply never touched survives revert.

    Real-git test: subdir/requirements.txt is committed and the operator dirtied
    it before apply ran.  apply only modifies package-lock.json (the npm lockfile
    captured in before_snap).  After revert:
      - subdir/requirements.txt still holds the operator's dirty content.
      - package-lock.json is restored to its pre-apply state.

    This guards against the data-loss bug where the checkout path filtered by
    _ARTIFACT_BASENAMES alone, causing any pre-existing dirty tracked file whose
    basename matches an artifact name to be silently reverted.
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

    # Operator dirty-edits requirements.txt before apply (simulates --allow-dirty run).
    req.write_text("flask==2.0.0\nrequests==2.28.0\n", encoding="utf-8")
    dirty_content = req.read_bytes()

    # Simulate apply modifying only the lockfile.
    lock = tmp_path / "package-lock.json"
    original_lock = lock.read_bytes()
    lock.write_text('{"lockfileVersion":2,"mutated":true}', encoding="utf-8")

    reverted_ok = revert_workspace_after_verify_fail(ws, before_snap)

    assert reverted_ok is True
    # Operator's pre-existing dirty edit must survive — apply never touched this file.
    assert req.read_bytes() == dirty_content, (
        "revert must not discard a pre-existing dirty tracked file whose basename is "
        "in _ARTIFACT_BASENAMES but that apply never touched (subdir/requirements.txt)"
    )
    # The lockfile apply DID modify must be restored (via snapshot restore path).
    assert lock.read_bytes() == original_lock, (
        "revert must restore the lockfile that apply modified (package-lock.json)"
    )


# ---------------------------------------------------------------------------
# Snapshot scope: pdm / pipenv / deno managers capture their lockfiles (F012)
# ---------------------------------------------------------------------------


def test_snapshot_captures_pdm_lock(tmp_path: Path) -> None:
    (tmp_path / "pdm.lock").write_text('[[package]]\nname = "x"\nversion = "1"\n')
    snap = snapshot_workspace_files(tmp_path, "pdm")
    assert any(p.name == "pdm.lock" for p in snap)


def test_snapshot_captures_pipfile_lock(tmp_path: Path) -> None:
    (tmp_path / "Pipfile.lock").write_text("{}")
    snap = snapshot_workspace_files(tmp_path, "pipenv")
    assert any(p.name == "Pipfile.lock" for p in snap)


def test_snapshot_captures_deno_lock(tmp_path: Path) -> None:
    (tmp_path / "deno.lock").write_text("{}")
    snap = snapshot_workspace_files(tmp_path, "deno")
    assert any(p.name == "deno.lock" for p in snap)
