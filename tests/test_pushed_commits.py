"""Lightweight tests for pushed_commits helpers (no network, no real git pushes)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from repo_ops.core.cli import parse_iso8601
from repo_ops.core.git import git_common_dir, repo_label
from repo_ops.pushed.audit import PUSH_RE


def test_parse_iso8601_valid_z_suffix():
    dt = parse_iso8601("2026-05-26T00:00Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 26


def test_parse_iso8601_valid_offset():
    dt = parse_iso8601("2026-05-26T12:00:00+00:00")
    assert dt == datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)


def test_parse_iso8601_rejects_naive():
    with pytest.raises(argparse.ArgumentTypeError) as exc:
        parse_iso8601("2026-05-26T00:00")
    assert "no timezone" in str(exc.value)


def test_parse_iso8601_rejects_invalid():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_iso8601("not-a-date")


def test_push_re_matches_update_by_push():
    line = "abc123def4567890abcdef1234567890abcdef12 refs/remotes/origin/main@{0}: update by push"
    m = PUSH_RE.match(line)
    assert m is not None
    assert m.group(1) == "abc123def4567890abcdef1234567890abcdef12"
    assert m.group(2) == "0"


def test_push_re_ignores_non_push():
    line = "abc123 refs/remotes/origin/main@{1}: checkout: moving from ..."
    assert PUSH_RE.match(line) is None


def test_repo_label_with_monkeypatch(monkeypatch):
    # Use real function but patch Path.home and resolve to control
    fake_home = Path("/home/testuser")
    fake_repo = Path("/usr/local/src/com.github/Rethunk-Tech/repo-ops")

    def fake_resolve(self):
        return self

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    label = repo_label(str(fake_repo), ["/usr/local/src"])
    assert label == "com.github/Rethunk-Tech/repo-ops"


def test_git_common_dir_monkeypatch(monkeypatch):
    calls = []

    def fake_run_git(repo, *args, **kwargs):
        calls.append((repo, args))

        class Fake:
            returncode = 0
            stdout = "/real/common\n"

        return Fake()

    monkeypatch.setattr("repo_ops.core.git.run_git", fake_run_git)
    result = git_common_dir("/some/repo")
    assert result == "/real/common"
    assert len(calls) == 1
