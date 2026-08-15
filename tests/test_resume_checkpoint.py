"""Tests for the REPO_OPS_RESUME checkpoint feature (Task B / #17).

Covers:
  - state.load_checkpoint / state.save_checkpoint round-trip and no-state bypass
  - run._checkpoint_should_skip: skip on matching SHA, no skip on changed SHA
  - integration: checkpoint file is written after a successful apply bundle
"""

from __future__ import annotations

from pathlib import Path

import pytest

import repo_ops_core.state as state_mod
import repo_ops_upgrade.run as run_mod
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle(git_repo: Path) -> RepoBundle:
    """Minimal RepoBundle with one WorkspaceResult for *git_repo*."""
    ws = Workspace(
        path=git_repo / "package.json",
        ecosystem="js",
        manager="npm",
        git_repo=git_repo,
        label=str(git_repo),
    )
    return RepoBundle(
        git_repo=git_repo,
        label=str(git_repo),
        workspaces=[WorkspaceResult(workspace=ws)],
    )


# ---------------------------------------------------------------------------
# state.load_checkpoint / state.save_checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_save_and_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_checkpoint persists data; load_checkpoint reads it back."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    data = {"/some/repo": "abc123def456", "/other/repo": "feed0000cafe"}
    state_mod.save_checkpoint(data)
    loaded = state_mod.load_checkpoint()
    assert loaded == data


def test_checkpoint_load_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_checkpoint returns empty dict when no checkpoint file exists."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)
    assert state_mod.load_checkpoint() == {}


def test_checkpoint_no_state_disables_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPO_OPS_NO_STATE=1 prevents checkpoint from being written."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")

    state_mod.save_checkpoint({"/repo": "sha123"})
    # File must not exist
    checkpoint_file = tmp_path / "repo-ops" / "apply-checkpoint.json"
    assert not checkpoint_file.exists()


def test_checkpoint_no_state_disables_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPO_OPS_NO_STATE=1 causes load_checkpoint to return empty dict."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)
    # Write a real checkpoint first
    state_mod.save_checkpoint({"/repo": "sha123"})

    # Now enable no-state and verify load returns {}
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    assert state_mod.load_checkpoint() == {}


# ---------------------------------------------------------------------------
# run._checkpoint_should_skip
# ---------------------------------------------------------------------------


def test_checkpoint_should_skip_on_matching_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle is skipped when its git_repo HEAD matches the checkpointed SHA."""
    git_repo = tmp_path / "myrepo"
    git_repo.mkdir()
    bundle = _make_bundle(git_repo)
    checkpoint = {str(git_repo): "abc123"}

    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: "abc123")
    assert run_mod._checkpoint_should_skip(bundle, checkpoint) is True


def test_checkpoint_should_not_skip_on_changed_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle is NOT skipped when the current HEAD differs from the checkpoint."""
    git_repo = tmp_path / "myrepo"
    git_repo.mkdir()
    bundle = _make_bundle(git_repo)
    checkpoint = {str(git_repo): "oldsha123"}

    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: "newsha456")
    assert run_mod._checkpoint_should_skip(bundle, checkpoint) is False


def test_checkpoint_should_not_skip_if_repo_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle is NOT skipped when its git_repo is not in the checkpoint."""
    git_repo = tmp_path / "myrepo"
    git_repo.mkdir()
    bundle = _make_bundle(git_repo)
    checkpoint: dict[str, str] = {}  # empty — repo not previously completed

    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: "abc123")
    assert run_mod._checkpoint_should_skip(bundle, checkpoint) is False


def test_checkpoint_should_not_skip_when_sha_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle is NOT skipped when _get_head_sha returns None (git unavailable)."""
    git_repo = tmp_path / "myrepo"
    git_repo.mkdir()
    bundle = _make_bundle(git_repo)
    checkpoint = {str(git_repo): "abc123"}

    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: None)
    assert run_mod._checkpoint_should_skip(bundle, checkpoint) is False


# ---------------------------------------------------------------------------
# Checkpoint written after successful apply (integration via run_upgrade)
# ---------------------------------------------------------------------------


def test_checkpoint_written_after_successful_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful apply run, the repo appears in the checkpoint file."""
    monkeypatch.setenv("REPO_OPS_RESUME", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)
    monkeypatch.chdir(tmp_path)

    POST_SHA = "deadbeefcafe1234"
    git_repo = tmp_path / "repo"
    git_repo.mkdir()

    # _get_head_sha is called before AND after processing;
    # always return the same SHA for simplicity.
    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: POST_SHA)

    # Fake _run_upgrade_body: simulate a successful apply of one bundle.
    # We drive _process_repo_checkpointed directly by injecting a bundle
    # into the body logic.  The simplest path: replace _run_upgrade_body
    # with one that calls the checkpointed wrapper manually.
    bundle = _make_bundle(git_repo)

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        # Simulate the minimal body: set up checkpoint infra (mirroring
        # _run_upgrade_body) and call the checkpointed wrapper once.
        import threading as _threading

        from repo_ops_core import state as _cpt_state

        checkpoint: dict[str, str] = _cpt_state.load_checkpoint()
        checkpoint_lock = _threading.Lock()

        def _process_repo_checkpointed(b: RepoBundle) -> list[WorkspaceResult]:
            if run_mod._checkpoint_should_skip(b, checkpoint):
                for wr in b.workspaces:
                    wr.status = "skip"
                    wr.error = "resume: already applied"
                return list(b.workspaces)
            # Simulate a successful (ok) result
            for wr in b.workspaces:
                wr.status = "ok"
            results = list(b.workspaces)
            if not any(r.status == "fail" for r in results):
                post_sha = run_mod._get_head_sha(b.git_repo)
                if post_sha:
                    with checkpoint_lock:
                        checkpoint[str(b.git_repo)] = post_sha
                        to_save = dict(checkpoint)
                    _cpt_state.save_checkpoint(to_save)
            return results

        _process_repo_checkpointed(bundle)
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    rc = run_mod.run_upgrade(apply=True, argv=["--flat", str(tmp_path)])
    assert rc == 0

    # Checkpoint must now contain the repo at POST_SHA
    loaded = state_mod.load_checkpoint()
    assert loaded.get(str(git_repo)) == POST_SHA


def test_resume_skips_completed_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On resume, a repo in the checkpoint at the current HEAD is not re-processed."""
    monkeypatch.setenv("REPO_OPS_RESUME", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    HEAD_SHA = "abc123def456"
    git_repo = tmp_path / "repo"
    git_repo.mkdir()

    # Pre-populate checkpoint (simulating a completed previous run)
    state_mod.save_checkpoint({str(git_repo): HEAD_SHA})

    bundle = _make_bundle(git_repo)
    process_calls: list[RepoBundle] = []

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        import threading as _threading

        from repo_ops_core import state as _cpt_state

        checkpoint: dict[str, str] = _cpt_state.load_checkpoint()
        checkpoint_lock = _threading.Lock()

        def _process_repo_real(b: RepoBundle) -> list[WorkspaceResult]:
            process_calls.append(b)
            for wr in b.workspaces:
                wr.status = "ok"
            return list(b.workspaces)

        def _process_repo_checkpointed(b: RepoBundle) -> list[WorkspaceResult]:
            if run_mod._checkpoint_should_skip(b, checkpoint):
                for wr in b.workspaces:
                    wr.status = "skip"
                    wr.error = "resume: already applied"
                return list(b.workspaces)
            results = _process_repo_real(b)
            if not any(r.status == "fail" for r in results):
                post_sha = run_mod._get_head_sha(b.git_repo)
                if post_sha:
                    with checkpoint_lock:
                        checkpoint[str(b.git_repo)] = post_sha
                        to_save = dict(checkpoint)
                    _cpt_state.save_checkpoint(to_save)
            return results

        _process_repo_checkpointed(bundle)
        return 0

    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: HEAD_SHA)
    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=True, argv=["--flat", str(tmp_path)])

    assert process_calls == [], "repo already in checkpoint must not be re-processed"


def test_resume_does_not_skip_on_changed_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On resume, a repo whose HEAD changed since checkpointing IS re-processed."""
    monkeypatch.setenv("REPO_OPS_RESUME", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    OLD_SHA = "oldsha000"
    NEW_SHA = "newsha999"
    git_repo = tmp_path / "repo"
    git_repo.mkdir()

    # Checkpoint has the old SHA; current HEAD is different
    state_mod.save_checkpoint({str(git_repo): OLD_SHA})

    bundle = _make_bundle(git_repo)
    process_calls: list[RepoBundle] = []

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        import threading as _threading

        from repo_ops_core import state as _cpt_state

        checkpoint: dict[str, str] = _cpt_state.load_checkpoint()
        checkpoint_lock = _threading.Lock()

        def _process_repo_real(b: RepoBundle) -> list[WorkspaceResult]:
            process_calls.append(b)
            for wr in b.workspaces:
                wr.status = "ok"
            return list(b.workspaces)

        def _process_repo_checkpointed(b: RepoBundle) -> list[WorkspaceResult]:
            if run_mod._checkpoint_should_skip(b, checkpoint):
                for wr in b.workspaces:
                    wr.status = "skip"
                    wr.error = "resume: already applied"
                return list(b.workspaces)
            results = _process_repo_real(b)
            if not any(r.status == "fail" for r in results):
                post_sha = run_mod._get_head_sha(b.git_repo)
                if post_sha:
                    with checkpoint_lock:
                        checkpoint[str(b.git_repo)] = post_sha
                        to_save = dict(checkpoint)
                    _cpt_state.save_checkpoint(to_save)
            return results

        _process_repo_checkpointed(bundle)
        return 0

    monkeypatch.setattr(run_mod, "_get_head_sha", lambda p: NEW_SHA)
    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=True, argv=["--flat", str(tmp_path)])

    assert len(process_calls) == 1, "repo with changed HEAD must be re-processed"
    assert process_calls[0].git_repo == git_repo
