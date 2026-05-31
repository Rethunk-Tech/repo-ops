"""Scan root validation and find-symlink discovery flags."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.cli import looks_like_find_predicate, resolve_scan_roots
from repo_ops_core.discovery import discover_repos


def test_looks_like_find_predicate() -> None:
    assert looks_like_find_predicate("-name")
    assert looks_like_find_predicate("/tmp -name .git")
    assert not looks_like_find_predicate("/usr/local/src")
    assert not looks_like_find_predicate("")


def test_resolve_scan_roots_rejects_missing(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit):
        resolve_scan_roots([str(missing)], parser)


def test_resolve_scan_roots_rejects_find_predicate(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        resolve_scan_roots(["-name", ".git"], parser)


def test_resolve_scan_roots_resolves_existing_dir(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    resolved = resolve_scan_roots([str(tmp_path)], parser)
    assert resolved == [str(tmp_path.resolve())]


def test_discover_repos_follow_symlinks_flag(tmp_path: Path) -> None:
    repo_dir = tmp_path / "proj"
    (repo_dir / ".git").mkdir(parents=True)
    common = str((repo_dir / ".git").resolve())

    def fake_git_common_dir(_p: str) -> str:
        return common

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir),
        patch("repo_ops_core.discovery.subprocess.run", fake_run),
    ):
        discover_repos([str(tmp_path)], follow_symlinks=False)
        discover_repos([str(tmp_path)], follow_symlinks=True)

    assert "-L" not in captured[0]
    assert "-L" in captured[1]
