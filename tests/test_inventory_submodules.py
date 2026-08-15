"""Coverage for count_submodule (git submodule gitlink pin counting).

Fixture repos are built with real git commands: ``git update-index
--add --cacheinfo 160000,<sha>,<path>`` stages a gitlink tree entry
directly (mode 160000) without requiring an actual submodule checkout,
mirroring what a real ``git submodule add`` commit looks like at the
tree level.
"""

import subprocess
from pathlib import Path

from repo_ops_inventory.counting import count_submodule

_FAKE_SHA = "a" * 40


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_count_submodule_finds_gitlink_pin(tmp_path: Path, init_git_repo) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)

    (repo / ".gitmodules").write_text(
        '[submodule "vendor/sub"]\n'
        "\tpath = vendor/sub\n"
        "\turl = https://example.com/sub.git\n"
    )
    _git(repo, "add", ".gitmodules")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{_FAKE_SHA},vendor/sub")
    _git(repo, "commit", "-q", "-m", "init")

    direct, versions = count_submodule(repo)

    assert direct == 1
    assert versions == {f"vendor/sub@{_FAKE_SHA}"}


def test_count_submodule_no_gitmodules_returns_zero(tmp_path: Path, init_git_repo) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")

    assert count_submodule(repo) == (0, set())


def test_count_submodule_non_git_directory_returns_zero(tmp_path: Path) -> None:
    """A .gitmodules file with no enclosing git repo resolves to no pins."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / ".gitmodules").write_text(
        '[submodule "vendor/sub"]\n\tpath = vendor/sub\n\turl = https://example.com/sub.git\n'
    )

    assert count_submodule(plain) == (0, set())
