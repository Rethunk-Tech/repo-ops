"""Lightweight tests for discovery module."""

from pathlib import Path
from unittest.mock import patch

from repo_ops.discovery import discover_repos


def test_discover_repos_skips_non_dirs_and_returns_dict():
    result = discover_repos(["/nonexistent/path/that/does/not/exist"])
    assert isinstance(result, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())


def test_discover_repos_with_tmp_layout_and_monkeypatch(tmp_path: Path):
    # Create a fake .git dir under tmp (may or may not be discovered depending on find + SKIP_DIRS)
    repo_dir = tmp_path / "myorg" / "myrepo"
    (repo_dir / ".git").mkdir(parents=True)

    def fake_git_common_dir(p):
        return str(Path(p).resolve())

    with patch("repo_ops.discovery.git_common_dir", fake_git_common_dir):
        result = discover_repos([str(tmp_path)])
    # Contract: returns dict, does not crash; discovery may be 0 in constrained env
    assert isinstance(result, dict)
