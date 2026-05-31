"""Lightweight tests for discovery module."""

from pathlib import Path
from unittest.mock import patch

from repo_ops.core.discovery import discover_repos
from repo_ops.core.walk import iter_scanned_dirs, manifest_kinds


def test_discover_repos_skips_non_dirs_and_returns_dict():
    result = discover_repos(["/nonexistent/path/that/does/not/exist"])
    assert isinstance(result, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())


def test_discover_repos_with_tmp_layout_and_monkeypatch(tmp_path: Path):
    repo_dir = tmp_path / "myorg" / "myrepo"
    (repo_dir / ".git").mkdir(parents=True)
    common = str((repo_dir / ".git").resolve())

    def fake_git_common_dir(p):
        return common

    with patch("repo_ops.core.discovery.git_common_dir", fake_git_common_dir):
        result = discover_repos([str(tmp_path)])
    assert isinstance(result, dict)
    assert common in result
    assert result[common] == str(repo_dir.resolve())


def test_manifest_kinds_detects_ecosystems():
    assert manifest_kinds({"package.json"}) == ["node"]
    assert manifest_kinds({"go.mod"}) == ["go"]
    assert manifest_kinds({"Cargo.toml"}) == ["rust"]
    assert manifest_kinds({"requirements.txt"}) == ["py"]
    assert manifest_kinds(set()) == []


def test_iter_scanned_dirs_skips_node_modules(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "package.json").write_text("{}")
    (root / "package.json").write_text("{}")
    dirs = [mdir for mdir, _ in iter_scanned_dirs(root)]
    assert root in dirs
    assert not any("node_modules" in str(p) for p in dirs)
