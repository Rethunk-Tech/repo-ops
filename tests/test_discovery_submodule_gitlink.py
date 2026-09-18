"""discover_repos must find git submodules, whose .git is a *file* (gitlink),
not just normal repos, whose .git is a directory.

A `find -type d -name .git` predicate cannot match a gitlink, so an umbrella
repo's submodules (Bastion has ~30) would be invisible without the file case.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_core.discovery import discover_repos


def _init_repo(path: Path) -> None:
    (path / ".git").mkdir(parents=True)


def _init_gitlink_submodule(path: Path, superproject_git_dir: str) -> None:
    path.mkdir(parents=True)
    (path / ".git").write_text(f"gitdir: {superproject_git_dir}\n", encoding="utf-8")


def test_discover_repos_finds_normal_repo_via_dir_git(tmp_path: Path) -> None:
    repo = tmp_path / "normal-repo"
    _init_repo(repo)

    with patch(
        "repo_ops_core.discovery.git_common_dir",
        lambda p: str((Path(p) / ".git").resolve()),
    ):
        found = discover_repos([str(tmp_path)])

    assert str(repo.resolve()) in found.values()


def test_discover_repos_finds_submodule_via_file_git(tmp_path: Path) -> None:
    """The real bug: a submodule's .git is a file, not a directory."""
    superproject = tmp_path / "superproject"
    _init_repo(superproject)
    submodule = superproject / "sub"
    _init_gitlink_submodule(submodule, str((superproject / ".git" / "modules" / "sub").resolve()))

    with patch(
        "repo_ops_core.discovery.git_common_dir",
        lambda p: str(Path(p).resolve()),
    ):
        found = discover_repos([str(tmp_path)])

    assert str(submodule.resolve()) in found.values(), f"submodule not discovered; found: {found}"
    assert str(superproject.resolve()) in found.values()


def test_discover_repos_finds_nested_submodule_of_submodule(tmp_path: Path) -> None:
    """A submodule can itself contain further nested submodules — find must keep
    walking past a gitlink match (it only prunes the .git entry itself, which has
    no children to walk into whether it's a directory or a gitlink file)."""
    superproject = tmp_path / "superproject"
    _init_repo(superproject)
    submodule = superproject / "sub"
    _init_gitlink_submodule(submodule, str((superproject / ".git" / "modules" / "sub").resolve()))
    nested = submodule / "deep"
    _init_gitlink_submodule(nested, str(submodule / ".git" / "modules" / "deep"))

    with patch(
        "repo_ops_core.discovery.git_common_dir",
        lambda p: str(Path(p).resolve()),
    ):
        found = discover_repos([str(tmp_path)])

    assert str(nested.resolve()) in found.values(), f"found: {found}"
