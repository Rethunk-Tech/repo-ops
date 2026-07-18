"""Direct unit tests for repo_ops_core.git helpers."""

from __future__ import annotations

from pathlib import Path

from repo_ops_core.git import git_repo_for


def test_git_repo_for_finds_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "pkg" / "src"
    nested.mkdir(parents=True)

    assert git_repo_for(nested) == repo.resolve()
    assert git_repo_for(repo) == repo.resolve()


def test_git_repo_for_returns_none_outside_repo(tmp_path: Path) -> None:
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    assert git_repo_for(orphan) is None


def test_git_repo_for_path_dot_self_reference(tmp_path: Path) -> None:
    """A worktree whose path is the repo root (path=.) still resolves."""
    repo = tmp_path / "umbrella"
    (repo / ".git").mkdir(parents=True)
    assert git_repo_for(repo / ".") == repo.resolve()
