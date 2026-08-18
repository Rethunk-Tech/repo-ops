"""Tests for the REPO_OPS_RESUME checkpoint feature.

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
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult

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


def test_checkpoint_save_and_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_checkpoint persists data; load_checkpoint reads it back."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    data = {"/some/repo": "abc123def456", "/other/repo": "feed0000cafe"}
    state_mod.save_checkpoint(data)
    loaded = state_mod.load_checkpoint()
    assert loaded == data


def test_checkpoint_load_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_checkpoint returns empty dict when no checkpoint file exists."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)
    assert state_mod.load_checkpoint() == {}


def test_checkpoint_no_state_disables_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REPO_OPS_NO_STATE=1 prevents checkpoint from being written."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")

    state_mod.save_checkpoint({"/repo": "sha123"})
    # File must not exist
    checkpoint_file = tmp_path / "repo-ops" / "apply-checkpoint.json"
    assert not checkpoint_file.exists()


def test_checkpoint_no_state_disables_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    checkpoint: dict[str, str] = {}  # repo absent from checkpoint

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


def _stub_discovery_and_apply(
    monkeypatch: pytest.MonkeyPatch,
    bundle: RepoBundle,
    process_fn,
) -> None:
    """Point run_mod's discovery + apply steps at *bundle* and *process_fn*.

    Everything past this boundary (filtering, checkpoint skip/save, commit)
    is the real ``_run_upgrade_body`` code.
    """
    git_repo = bundle.git_repo
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "discover_all",
        lambda *a, **k: [wr.workspace for wr in bundle.workspaces],
    )
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "group_by_repo",
        lambda found, roots: {git_repo: bundle},
    )
    monkeypatch.setattr(run_mod.orchestration, "process_workspace", process_fn)


def test_checkpoint_written_after_successful_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    """After a successful apply+commit run, the repo appears in the checkpoint file."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    bundle = _make_bundle(git_repo)

    def _apply(wr: WorkspaceResult, **_kwargs: object) -> WorkspaceResult:
        (wr.workspace.git_repo / "package.json").write_text(
            '{"version": "0.0.1"}', encoding="utf-8"
        )
        wr.status = "ok"
        wr.changes = [Change(name="demo-dep", from_ver="1.0.0", to="1.0.1")]
        return wr

    _stub_discovery_and_apply(monkeypatch, bundle, _apply)

    rc = run_mod.run_upgrade(
        apply=True,
        argv=[
            "--flat",
            "--skip-actions",
            "--quiet",
            "--resume",
            "--commit",
            "conventional",
            str(git_repo),
        ],
    )
    assert rc == 0

    loaded = state_mod.load_checkpoint()
    assert loaded.get(str(git_repo)) == run_mod._get_head_sha(git_repo)


def test_resume_skips_completed_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    """On resume, a repo in the checkpoint at the current HEAD is not re-processed."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    head_sha = run_mod._get_head_sha(git_repo)
    # "_scope": "" matches the unfiltered run below, so the checkpoint isn't
    # discarded on the scope-mismatch check before the per-repo SHA check runs.
    state_mod.save_checkpoint({str(git_repo): head_sha, "_scope": ""})

    bundle = _make_bundle(git_repo)
    process_calls: list[Path] = []

    def _apply(wr: WorkspaceResult, **_kwargs: object) -> WorkspaceResult:
        process_calls.append(wr.workspace.git_repo)
        wr.status = "ok"
        return wr

    _stub_discovery_and_apply(monkeypatch, bundle, _apply)

    rc = run_mod.run_upgrade(
        apply=True,
        argv=[
            "--flat",
            "--skip-actions",
            "--quiet",
            "--resume",
            "--commit",
            "conventional",
            str(git_repo),
        ],
    )
    assert rc == 0
    assert process_calls == [], "repo already in checkpoint must not be re-processed"


def test_resume_does_not_skip_on_changed_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    """On resume, a repo whose HEAD changed since checkpointing IS re-processed."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    # Checkpoint holds a SHA that doesn't match the repo's real current HEAD.
    state_mod.save_checkpoint({str(git_repo): "stale-sha-not-current-head", "_scope": ""})

    bundle = _make_bundle(git_repo)
    process_calls: list[Path] = []

    def _apply(wr: WorkspaceResult, **_kwargs: object) -> WorkspaceResult:
        process_calls.append(wr.workspace.git_repo)
        wr.status = "ok"
        return wr

    _stub_discovery_and_apply(monkeypatch, bundle, _apply)

    rc = run_mod.run_upgrade(
        apply=True,
        argv=[
            "--flat",
            "--skip-actions",
            "--quiet",
            "--resume",
            "--commit",
            "conventional",
            str(git_repo),
        ],
    )
    assert rc == 0
    assert process_calls == [git_repo], "repo with changed HEAD must be re-processed"
