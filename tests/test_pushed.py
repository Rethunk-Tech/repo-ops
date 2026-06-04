"""Lightweight tests for repo-ops pushed helpers (no network, no real git pushes)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pytest
from repo_ops_core.cli import parse_iso8601
from repo_ops_core.discovery import discover_repos
from repo_ops_core.git import git_common_dir, repo_label
from repo_ops_pushed.audit import (
    PUSH_RE,
    Filters,
    _reflog_pre_sha,
    collect_results,
    pushed_commits_for_repo,
)


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


def test_discover_repos_finds_dot_git(tmp_path: Path):
    repo_dir = tmp_path / "proj"
    (repo_dir / ".git").mkdir(parents=True)
    common = str((repo_dir / ".git").resolve())

    def fake_git_common_dir(p):
        return common

    from unittest.mock import patch

    with patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir):
        found = discover_repos([str(tmp_path)])
    assert found[common] == str(repo_dir.resolve())


def test_reflog_pre_sha_uses_rev_parse_parent(monkeypatch):
    new_sha = "b" * 40
    old_sha = "a" * 40
    ref = "refs/remotes/origin/main"

    def fake_run_git(repo, *args, **kwargs):
        class Fake:
            returncode = 0
            stdout = ""

        if args[:2] == ("reflog", "show"):
            Fake.stdout = f"{new_sha} {ref}@{{0}}: update by push\n"
        elif args[0] == "rev-parse":
            Fake.stdout = f"{old_sha}\n"
        return Fake()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    assert _reflog_pre_sha("/fake", ref, new_sha) == old_sha


def test_reflog_pre_sha_first_push_returns_none(monkeypatch):
    new_sha = "b" * 40
    ref = "refs/remotes/origin/main"

    def fake_run_git(repo, *args, **kwargs):
        class Fake:
            returncode = 0
            stdout = ""

        if args[:2] == ("reflog", "show"):
            Fake.stdout = f"{new_sha} {ref}@{{0}}: update by push\n"
        elif args[0] == "rev-parse":
            Fake.returncode = 1
        return Fake()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    assert _reflog_pre_sha("/fake", ref, new_sha) is None


def test_pushed_commits_for_repo_first_push_uses_root(monkeypatch):
    """Oldest push entry with no reflog parent lists all commits on the branch."""
    now = 1_700_000_000
    filters = Filters(since_unix=now - 3600, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"
    new_sha = "b" * 40
    commit_sha = "c" * 40

    def fake_run_git(repo, *args, **kwargs):
        class Fake:
            returncode = 0
            stdout = ""

        if cmd := args:
            if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
                Fake.stdout = f"{ref}\n"
            elif cmd[:2] == ("reflog", "show"):
                Fake.stdout = f"{new_sha} {ref}@{{{now}}}: update by push\n"
            elif cmd[0] == "rev-parse":
                Fake.returncode = 1
            elif cmd[0] == "rev-list" and new_sha in cmd:
                Fake.stdout = f"{commit_sha}\n"
        return Fake()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    commits = pushed_commits_for_repo("/fake/repo", filters)
    assert commit_sha in commits


def test_pushed_commits_for_repo_from_push_reflog(monkeypatch):
    """Regression: remote-tracking reflog 'update by push' yields commit SHAs."""
    now = 1_700_000_000
    filters = Filters(since_unix=now - 3600, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"
    old_sha = "a" * 40
    new_sha = "b" * 40
    commit_sha = "c" * 40

    def fake_run_git(repo, *args, **kwargs):
        cmd = args

        class Fake:
            returncode = 0
            stdout = ""

        if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
            Fake.stdout = f"{ref}\n"
        elif cmd[:2] == ("reflog", "show"):
            Fake.stdout = (
                f"{new_sha[:7]} {ref}@{{{now}}}: update by push\n"
                f"{old_sha[:7]} {ref}@{{{now - 100}}}: update by push\n"
            )
        elif cmd[0] == "rev-list":
            Fake.stdout = f"{commit_sha}\n"
        return Fake()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    commits = pushed_commits_for_repo("/fake/repo", filters)
    assert commit_sha in commits


def test_timeout_in_reflog_pre_sha_does_not_abort_scan(monkeypatch):
    """Regression #41: TimeoutExpired in _reflog_pre_sha must not propagate through
    future.result() and abort the whole scan; pushed_commits_for_repo returns gracefully.

    Setup: single ref, single oldest push entry (i+1 >= len(entries)) so the code
    falls through to _reflog_pre_sha. reflog show inside _reflog_pre_sha returns a
    matching tip line, driving it to the rev-parse call, which times out.
    Expected: no exception raised; commits dict is returned (possibly empty but intact).
    """
    import subprocess

    now = 1_700_000_000
    filters = Filters(since_unix=now - 3600, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"
    new_sha = "b" * 40
    commit_sha = "c" * 40

    def fake_run_git(repo, *args, **kwargs):
        class Fake:
            returncode = 0
            stdout = ""

        cmd = args
        if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
            Fake.stdout = f"{ref}\n"
        elif cmd[:2] == ("reflog", "show") and "--date=unix" in cmd:
            # Called from pushed_commits_for_repo — single entry, so hits _reflog_pre_sha
            Fake.stdout = f"{new_sha} {ref}@{{{now}}}: update by push\n"
        elif cmd[:2] == ("reflog", "show"):
            # Called from _reflog_pre_sha — return matching tip to reach rev-parse
            Fake.stdout = f"{new_sha} {ref}@{{0}}: update by push\n"
        elif cmd[0] == "rev-parse":
            # Simulate timeout — this is the newly guarded site
            raise subprocess.TimeoutExpired(cmd=["git", "rev-parse"], timeout=10)
        elif cmd[0] == "rev-list":
            Fake.stdout = f"{commit_sha}\n"
        return Fake()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    # Must not raise; scan degrades gracefully (old_sha falls back to "--root")
    commits = pushed_commits_for_repo("/fake/repo", filters)
    # The scan continues: commit_sha should still be found via --root fallback
    assert commit_sha in commits


def test_collect_results_uses_discovery(tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "r"
    (repo_dir / ".git").mkdir(parents=True)
    common = str((repo_dir / ".git").resolve())

    def fake_git_common_dir(p):
        return common

    def fake_pushed(repo, filters):
        return {"deadbeef" * 5: None}

    from unittest.mock import patch

    with (
        patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir),
        patch("repo_ops_pushed.audit.pushed_commits_for_repo", fake_pushed),
    ):
        all_commits, per_repo = collect_results([str(tmp_path)], Filters(0, 2**31))
    assert len(all_commits) == 1
    assert len(per_repo) == 1


def test_git_common_dir_monkeypatch(monkeypatch):
    calls = []

    def fake_run_git(repo, *args, **kwargs):
        calls.append((repo, args))

        class Fake:
            returncode = 0
            stdout = "/real/common\n"

        return Fake()

    monkeypatch.setattr("repo_ops_core.git.run_git", fake_run_git)
    result = git_common_dir("/some/repo")
    assert result == "/real/common"
    assert len(calls) == 1
