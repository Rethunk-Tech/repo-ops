"""Lightweight tests for discovery module."""

from repo_ops.discovery import discover_repos


def test_discover_repos_skips_non_dirs_and_returns_dict():
    # Non-existent or file roots should not crash and return empty or partial
    result = discover_repos(["/nonexistent/path/that/does/not/exist"])
    assert isinstance(result, dict)
    # Should not raise; basic contract
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())
