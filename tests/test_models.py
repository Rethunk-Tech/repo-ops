"""Unit tests for repo_ops_models.models: RepoBundle.any_dirty_skip and the
shared DIRTY_SKIP_MARKER constant it depends on.
"""

from __future__ import annotations

from pathlib import Path

import repo_ops_models.models as models_mod
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult


def _ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/repo"),
        ecosystem="py",
        manager="uv",
        git_repo=Path("/tmp/repo"),
        label="",
    )


def test_any_dirty_skip_true_when_marker_present() -> None:
    wr = WorkspaceResult(
        workspace=_ws(),
        status="skip",
        error=f"{models_mod.DIRTY_SKIP_MARKER} (use --allow-dirty)",
    )
    bundle = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr])
    assert bundle.any_dirty_skip() is True


def test_any_dirty_skip_false_when_marker_absent() -> None:
    wr = WorkspaceResult(workspace=_ws(), status="skip", error="some other reason")
    bundle = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr])
    assert bundle.any_dirty_skip() is False


def test_any_dirty_skip_uses_shared_constant_not_a_hardcoded_literal(
    monkeypatch,
) -> None:
    """any_dirty_skip() must key off the module constant, not a duplicated
    literal -- so retargeting the constant changes detection accordingly.
    """
    monkeypatch.setattr(models_mod, "DIRTY_SKIP_MARKER", "totally-different-marker")
    wr = WorkspaceResult(
        workspace=_ws(), status="skip", error="dirty git worktree (use --allow-dirty)"
    )
    bundle = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr])
    # Old literal no longer matches the (patched) constant.
    assert bundle.any_dirty_skip() is False

    wr2 = WorkspaceResult(
        workspace=_ws(), status="skip", error="totally-different-marker present"
    )
    bundle2 = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr2])
    assert bundle2.any_dirty_skip() is True
