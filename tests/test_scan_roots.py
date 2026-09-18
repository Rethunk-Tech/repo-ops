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


# ---------------------------------------------------------------------------
# Default-roots fix: a missing DEFAULT root is skipped, not fatal (first-run
# experience on a machine without this built-in layout) — but only when the
# caller didn't name it explicitly, and only up to "all defaults missing".
# ---------------------------------------------------------------------------


def test_resolve_scan_roots_partial_defaults_returns_only_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "exists"
    existing.mkdir()
    missing = tmp_path / "nope"
    fake_defaults = [str(existing), str(missing)]
    monkeypatch.setattr("repo_ops_core.cli.DEFAULT_ROOTS", fake_defaults)

    parser = argparse.ArgumentParser()
    resolved = resolve_scan_roots(fake_defaults, parser)

    assert resolved == [str(existing.resolve())]


def test_resolve_scan_roots_all_defaults_missing_exits_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_defaults = [str(tmp_path / "a"), str(tmp_path / "b")]
    monkeypatch.setattr("repo_ops_core.cli.DEFAULT_ROOTS", fake_defaults)

    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        resolve_scan_roots(fake_defaults, parser)

    err = capsys.readouterr().err
    assert "none of the default roots exist on this machine" in err
    assert "e.g. `.` for the current checkout" in err


def test_resolve_scan_roots_explicit_missing_path_still_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly-named missing path is a typo worth failing on, unlike a
    missing built-in default — even when some default roots do exist."""
    existing = tmp_path / "exists"
    existing.mkdir()
    monkeypatch.setattr("repo_ops_core.cli.DEFAULT_ROOTS", [str(existing)])

    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        resolve_scan_roots([str(tmp_path / "typo")], parser)


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
