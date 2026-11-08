"""github_remote must match the host, not a substring of the URL."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from repo_ops_core.git import github_remote


def _init_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    return path


def _add_remote(repo: Path, name: str, url: str) -> None:
    subprocess.run(["git", "remote", "add", name, url], cwd=repo, check=True, capture_output=True)


def test_github_remote_rejects_lookalike_hosts(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "r")
    _add_remote(repo, "path", "https://evil.com/github.com/owner/repo.git")
    _add_remote(repo, "sub", "https://github.com.evil.com/owner/repo.git")
    _add_remote(repo, "scp", "git@evil.com:github.com/owner/repo.git")
    assert github_remote(repo) is None

    _add_remote(repo, "origin", "https://github.com/owner/repo.git")
    got = github_remote(repo)
    assert got is not None
    assert got.startswith("git@github.com:") or urlparse(got).hostname == "github.com"


def test_github_remote_accepts_ssh_forms(tmp_path: Path) -> None:
    scp = _init_repo(tmp_path / "scp")
    _add_remote(scp, "origin", "git@github.com:owner/repo.git")
    assert github_remote(scp) == "git@github.com:owner/repo.git"

    ssh = _init_repo(tmp_path / "ssh")
    _add_remote(ssh, "origin", "ssh://git@github.com/owner/repo.git")
    assert github_remote(ssh) == "ssh://git@github.com/owner/repo.git"
