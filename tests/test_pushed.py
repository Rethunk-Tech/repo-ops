"""Lightweight tests for repo-ops pushed helpers (no network, no real git pushes)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from repo_ops_core.cli import parse_iso8601
from repo_ops_core.discovery import discover_repos
from repo_ops_core.git import git_common_dir, repo_label
from repo_ops_pushed import audit
from repo_ops_pushed.audit import (
    PUSH_RE,
    Filters,
    RepoResult,
    _reflog_pre_sha,
    collect_results,
    main,
    print_report,
    pushed_commits_for_repo,
    resolve_subjects,
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
    assert dt == datetime(2026, 5, 26, 12, 0, tzinfo=UTC)


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


def test_reflog_pre_sha_uses_rev_parse_parent(monkeypatch, make_proc):
    new_sha = "b" * 40
    old_sha = "a" * 40
    ref = "refs/remotes/origin/main"

    # reflog_lines already provided by caller; only rev-parse goes through run_git
    reflog_lines = [f"{new_sha} {ref}@{{0}}: update by push"]

    def fake_run_git(repo, *args, **kwargs):
        if args[0] == "rev-parse":
            return make_proc(stdout=f"{old_sha}\n")
        return make_proc()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    assert _reflog_pre_sha("/fake", ref, new_sha, reflog_lines) == old_sha


def test_reflog_pre_sha_first_push_returns_none(monkeypatch, make_proc):
    new_sha = "b" * 40
    ref = "refs/remotes/origin/main"

    # reflog_lines already provided by caller; rev-parse returns non-zero (no parent)
    reflog_lines = [f"{new_sha} {ref}@{{0}}: update by push"]

    def fake_run_git(repo, *args, **kwargs):
        return make_proc(returncode=1)

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    assert _reflog_pre_sha("/fake", ref, new_sha, reflog_lines) is None


def test_reflog_pre_sha_abbreviated_sha_match(monkeypatch, make_proc):
    """_reflog_pre_sha matches when reflog has an abbreviated (7-char) tip SHA."""
    new_sha = "b" * 40
    old_sha = "a" * 40
    ref = "refs/remotes/origin/main"

    # Reflog line uses 7-char abbreviation (git default); new_sha.startswith(tip) path
    reflog_lines = [f"{new_sha[:7]} {ref}@{{3}}: update by push"]

    def fake_run_git(repo, *args, **kwargs):
        return make_proc(stdout=f"{old_sha}\n")

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    result = _reflog_pre_sha("/fake", ref, new_sha, reflog_lines)
    assert result == old_sha


def test_pushed_commits_for_repo_first_push_uses_root(monkeypatch, make_proc):
    """Oldest push entry with no reflog parent lists all commits on the branch."""
    now = 1_700_000_000
    filters = Filters(since_unix=now - 3600, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"
    new_sha = "b" * 40
    commit_sha = "c" * 40

    def fake_run_git(repo, *args, **kwargs):
        cmd = args
        if not cmd:
            return make_proc()
        if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
            return make_proc(stdout=f"{ref}\n")
        if cmd[:2] == ("reflog", "show") and "--date=unix" in cmd:
            return make_proc(stdout=f"{new_sha} {ref}@{{{now}}}: update by push\n")
        if cmd[:2] == ("reflog", "show"):
            # Plain reflog (no --date=unix) passed to _reflog_pre_sha
            return make_proc(stdout=f"{new_sha} {ref}@{{0}}: update by push\n")
        if cmd[0] == "rev-parse":
            return make_proc(returncode=1)
        if cmd[0] == "rev-list" and new_sha in cmd:
            return make_proc(stdout=f"{commit_sha}\n")
        return make_proc()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    commits = pushed_commits_for_repo("/fake/repo", filters)
    assert commit_sha in commits


def test_pushed_commits_for_repo_from_push_reflog(monkeypatch, make_proc):
    """Regression: remote-tracking reflog 'update by push' yields commit SHAs."""
    now = 1_700_000_000
    filters = Filters(since_unix=now - 3600, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"
    old_sha = "a" * 40
    new_sha = "b" * 40
    commit_sha = "c" * 40

    def fake_run_git(repo, *args, **kwargs):
        cmd = args
        if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
            return make_proc(stdout=f"{ref}\n")
        if cmd[:2] == ("reflog", "show") and "--date=unix" in cmd:
            # Timestamp-format reflog for push-entry filtering
            return make_proc(
                stdout=(
                    f"{new_sha[:7]} {ref}@{{{now}}}: update by push\n"
                    f"{old_sha[:7]} {ref}@{{{now - 100}}}: update by push\n"
                )
            )
        if cmd[:2] == ("reflog", "show"):
            # Plain (sequential-index) reflog passed to _reflog_pre_sha
            return make_proc(
                stdout=(
                    f"{new_sha[:7]} {ref}@{{0}}: update by push\n"
                    f"{old_sha[:7]} {ref}@{{1}}: update by push\n"
                )
            )
        if cmd[0] == "rev-list":
            return make_proc(stdout=f"{commit_sha}\n")
        return make_proc()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    commits = pushed_commits_for_repo("/fake/repo", filters)
    assert commit_sha in commits


def test_timeout_in_reflog_pre_sha_does_not_abort_scan(monkeypatch, make_proc):
    """Regression #41: TimeoutExpired in _reflog_pre_sha must not propagate through
    future.result() and abort the whole scan; pushed_commits_for_repo returns gracefully.

    Setup: single ref, single oldest push entry. pushed_commits_for_repo fetches the
    plain reflog (no --date=unix) and passes those lines to _reflog_pre_sha, which
    drives into the rev-parse call that times out.
    Expected: no exception raised; commits dict is returned (possibly empty but intact).
    """
    import subprocess

    now = 1_700_000_000
    filters = Filters(since_unix=now - 3600, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"
    new_sha = "b" * 40
    commit_sha = "c" * 40

    def fake_run_git(repo, *args, **kwargs):
        cmd = args
        if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
            return make_proc(stdout=f"{ref}\n")
        if cmd[:2] == ("reflog", "show") and "--date=unix" in cmd:
            # Fetched first in pushed_commits_for_repo for timestamp filtering
            return make_proc(stdout=f"{new_sha} {ref}@{{{now}}}: update by push\n")
        if cmd[:2] == ("reflog", "show"):
            # Fetched second in pushed_commits_for_repo (plain) for _reflog_pre_sha
            return make_proc(stdout=f"{new_sha} {ref}@{{0}}: update by push\n")
        if cmd[0] == "rev-parse":
            # Simulate timeout — _reflog_pre_sha still guards this site
            raise subprocess.TimeoutExpired(cmd=["git", "rev-parse"], timeout=10)
        if cmd[0] == "rev-list":
            return make_proc(stdout=f"{commit_sha}\n")
        return make_proc()

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


def test_pushed_commits_non_oldest_uses_reflog_pre_sha_not_prev_push_tip(
    monkeypatch, make_proc
):
    """Regression #92: non-oldest push entry uses _reflog_pre_sha, not previous push tip.

    Simulates two pushes with a fetch in between:
      full reflog: @{0} push2  @{1} fetch (colleague)  @{2} push1
    Push-only entries: [(ts2, push2_sha), (ts1, push1_sha)]

    Without the fix, push2's old_sha = push1_sha, so rev-list runs
    push1_sha..push2_sha and inflates the count by including commit_from_fetch.
    With the fix, _reflog_pre_sha resolves ref@{1} (= fetch_sha) as the true
    pre-push2 base, so rev-list runs fetch_sha..push2_sha and returns only
    commit_from_push2 — the colleague's commit is excluded.
    """
    now = 1_700_000_000
    filters = Filters(since_unix=now - 7200, until_unix=now + 3600)
    ref = "refs/remotes/origin/main"

    push1_sha = "1" * 40  # tip after first push (oldest)
    fetch_sha = "f" * 40  # tip after colleague fetch (between the two pushes)
    push2_sha = "2" * 40  # tip after second push (newest)

    commit_from_push1 = "e" * 40  # commit in first push (reachable from push1_sha)
    commit_from_push2 = "c" * 40  # commit in second push — must appear in results
    commit_from_fetch = "d" * 40  # colleague's fetched commit — must NOT appear

    # Full reflog (used by _reflog_pre_sha): all entries including fetch
    full_reflog = (
        f"{push2_sha} {ref}@{{0}}: update by push\n"
        f"{fetch_sha} {ref}@{{1}}: fetch\n"
        f"{push1_sha} {ref}@{{2}}: update by push\n"
    )
    # Filtered push-only reflog (used by pushed_commits_for_repo, with --date=unix)
    push_reflog = (
        f"{push2_sha} {ref}@{{{now}}}: update by push\n"
        f"{push1_sha} {ref}@{{{now - 3600}}}: update by push\n"
    )

    def fake_run_git(repo, *args, **kwargs):
        cmd = args
        if cmd[:2] == ("for-each-ref", "--format=%(refname)"):
            return make_proc(stdout=f"{ref}\n")
        if cmd[:2] == ("reflog", "show") and "--date=unix" in cmd:
            # Called from pushed_commits_for_repo to build push-only entries list
            return make_proc(stdout=push_reflog)
        if cmd[:2] == ("reflog", "show"):
            # Called from _reflog_pre_sha to walk the full reflog (no --date=unix)
            return make_proc(stdout=full_reflog)
        if cmd[0] == "rev-parse":
            # _reflog_pre_sha resolves ref@{n+1} for the entry matching new_sha:
            #   push2 is at @{0}  → resolves @{1} → fetch_sha  (correct pre-push2 base)
            #   push1 is at @{2}  → resolves @{3} → not found (returncode=1, oldest)
            spec = cmd[1]  # e.g. "refs/remotes/origin/main@{1}"
            m = re.search(r"@\{(\d+)\}", spec)
            n = int(m.group(1)) if m else -1
            if n == 1:
                return make_proc(stdout=f"{fetch_sha}\n")
            return make_proc(returncode=1)
        if cmd[0] == "rev-list":
            range_arg = next((a for a in cmd if ".." in a), None)
            if range_arg == f"{fetch_sha}..{push2_sha}":
                # Correct range for push2 — excludes fetched colleague commit
                return make_proc(stdout=f"{commit_from_push2}\n")
            if range_arg == f"{push1_sha}..{push2_sha}":
                # Wrong range (bug path) — would include colleague's fetched commit
                return make_proc(stdout=f"{commit_from_push2}\n{commit_from_fetch}\n")
            # push1 uses --root; return only push1's own commits
            return make_proc(stdout=f"{commit_from_push1}\n")
        return make_proc()

    monkeypatch.setattr("repo_ops_pushed.audit.run_git", fake_run_git)
    commits = pushed_commits_for_repo("/fake/repo", filters)

    # push2 must use fetch_sha as base (true pre-push tip) — commit_from_push2 present
    assert commit_from_push2 in commits
    # colleague's fetched commit must NOT appear (excluded by correct fetch_sha base)
    assert commit_from_fetch not in commits


def test_git_common_dir_monkeypatch(monkeypatch, make_proc):
    calls = []

    def fake_run_git(repo, *args, **kwargs):
        calls.append((repo, args))
        return make_proc(stdout="/real/common\n")

    monkeypatch.setattr("repo_ops_core.git.run_git", fake_run_git)
    result = git_common_dir("/some/repo")
    assert result == "/real/common"
    assert len(calls) == 1


# ── resolve_subjects ─────────────────────────────────────────────────────────


def test_resolve_subjects_truncates_and_handles_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_proc,
) -> None:
    """Covers the happy path, the >100-char truncation, and the
    subprocess.TimeoutExpired catch (~232-236) in one pass."""
    sha_ok = "a" * 40
    sha_long = "b" * 40
    sha_timeout = "c" * 40
    repo = RepoResult(
        label="r1",
        path="/fake/repo1",
        commits={sha_ok: None, sha_long: None, sha_timeout: None},
    )
    long_subject = "x" * 150

    def fake_run_git(repo_path, *args, **kwargs):
        sha = args[-1]
        if sha == sha_ok:
            return make_proc(stdout="short subject\n")
        if sha == sha_long:
            return make_proc(stdout=long_subject + "\n")
        if sha == sha_timeout:
            raise subprocess.TimeoutExpired(cmd=["git", "log"], timeout=10)
        return make_proc()

    monkeypatch.setattr(audit, "run_git", fake_run_git)
    result = resolve_subjects([repo])

    subs = result["/fake/repo1"]
    assert subs[sha_ok] == "short subject"
    assert subs[sha_long] == "x" * 100 + "…"
    assert subs[sha_timeout] == ""
    assert f"WARN: skipped /fake/repo1 (log {sha_timeout[:8]}): timeout" in (
        capsys.readouterr().err
    )


# ── print_report ─────────────────────────────────────────────────────────────


def test_print_report_no_pushes(capsys: pytest.CaptureFixture[str]) -> None:
    print_report({}, [], "24 hours", ["/root"], top_n=10)
    assert "No pushes recorded in the window." in capsys.readouterr().out


def test_print_report_overflow_shas_and_messages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """3 repos with top_n=2 exercises the '(N other repos)' overflow row, plus
    --show-shas/--show-messages per-commit dump (with and without a subject)."""
    repos = [
        RepoResult(label=f"r{i}", path=f"/fake/r{i}", commits={f"{i:040x}": None})
        for i in range(3)
    ]
    all_commits: dict[str, None] = {}
    for r in repos:
        all_commits.update(r.commits)

    monkeypatch.setattr(
        audit,
        "resolve_subjects",
        lambda per_repo: {
            r.path: dict.fromkeys(r.commits, f"subject-{r.label}") for r in per_repo
        },
    )

    print_report(
        all_commits,
        repos,
        "24 hours",
        ["/root"],
        top_n=2,
        show_shas=True,
        show_messages=True,
    )
    out = capsys.readouterr().out
    # Overflow row for the 1 repo beyond top_n=2 (label column is narrow so the
    # "(1 other repos)" text is truncated — just assert the overflow count shows).
    assert "(1" in out
    assert "Total unique commits pushed: 3" in out
    # Every repo (not just the top-N shown in the table) gets its commits dumped.
    assert "subject-r0" in out
    assert "subject-r2" in out


# ── main() — window resolution, --json-out, --count-only ───────────────────


def test_main_window_resolution_and_hours_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(audit, "collect_results", lambda *a, **kw: ({}, []))
    root = str(tmp_path)

    # Neither --since/--until: rolling --hours window, no warning.
    assert main([root, "--hours", "2"]) == 0
    captured = capsys.readouterr()
    assert "2 hours" in captured.out
    assert "WARNING" not in captured.err

    # --since only + --hours: existing warning ("without --until").
    assert main([root, "--since", "2026-01-01T00:00Z", "--hours", "3"]) == 0
    err = capsys.readouterr().err
    assert (
        "WARNING: --hours is ignored because --since is provided without --until" in err
    )

    # --until only: window = [now - hours, until); --hours legitimately used, no warning.
    assert main([root, "--until", "2099-01-01T00:00Z", "--hours", "5"]) == 0
    assert "WARNING" not in capsys.readouterr().err

    # --since + --until + --hours: --hours silently dropped previously; now warns (the fix).
    assert (
        main(
            [
                root,
                "--since",
                "2026-01-01T00:00Z",
                "--until",
                "2026-02-01T00:00Z",
                "--hours",
                "4",
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert (
        "WARNING: --hours is ignored because --since and --until are both provided"
        in err
    )


def test_main_json_out_requires_json_flag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--json-out", str(tmp_path / "out.json")])


def test_main_count_only_prints_total(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        audit, "collect_results", lambda *a, **kw: ({"a": None, "b": None}, [])
    )
    assert main([str(tmp_path), "--count-only"]) == 0
    assert capsys.readouterr().out.strip() == "2"


def test_main_json_out_writes_report_with_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    repo = RepoResult(label="repo1", path="/fake/repo1", commits={"d" * 40: None})
    monkeypatch.setattr(
        audit, "collect_results", lambda *a, **kw: ({"d" * 40: None}, [repo])
    )
    monkeypatch.setattr(
        audit,
        "resolve_subjects",
        lambda per_repo: {"/fake/repo1": {"d" * 40: "fix: something"}},
    )

    out_file = tmp_path / "report.json"
    rc = main(
        [
            str(tmp_path),
            "--json",
            "--show-messages",
            "--json-out",
            str(out_file),
        ]
    )
    assert rc == 0
    payload = json.loads(out_file.read_text())
    assert payload["total"] == 1
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["repos_with_pushes"] == 1
    assert payload["repos"][0]["commits_detail"][0]["subject"] == "fix: something"
    assert capsys.readouterr().out.strip() == str(out_file.resolve())


def test_pushed_compact_schema_version_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F088: --compact is single-line; schema_version is the first key."""
    import json

    monkeypatch.setattr(audit, "collect_results", lambda *a, **kw: ({}, []))
    rc = main([str(tmp_path), "--json", "--compact"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    payload = json.loads(out)
    assert next(iter(payload)) == "schema_version"
    assert payload["schema_version"] == 1
    assert "summary" in payload


def test_pushed_empty_author_rejected(tmp_path: Path) -> None:
    """F090: empty --author is a parser error."""
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--author", ""])


def test_pushed_since_after_until_rejected(tmp_path: Path) -> None:
    """F090: since >= until is a parser error."""
    with pytest.raises(SystemExit):
        main(
            [
                str(tmp_path),
                "--since",
                "2026-06-01T00:00Z",
                "--until",
                "2026-01-01T00:00Z",
            ]
        )


def test_pushed_show_shas_without_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F090: --show-shas prints short SHAs without requiring --show-messages."""
    sha = "abcdef0123456789" + "0" * 24
    repo = RepoResult(label="repo1", path="/fake/repo1", commits={sha: None})
    monkeypatch.setattr(
        audit, "collect_results", lambda *a, **kw: ({sha: None}, [repo])
    )
    assert main([str(tmp_path), "--show-shas"]) == 0
    out = capsys.readouterr().out
    assert "abcdef01" in out
    assert "Commits by repo:" in out


def test_pushed_no_merges_and_author_passed_to_filters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F090: --author and --no-merges reach Filters."""
    seen: list[Filters] = []

    def fake_collect(roots, filters, **kwargs):
        seen.append(filters)
        return {}, []

    monkeypatch.setattr(audit, "collect_results", fake_collect)
    assert (
        main([str(tmp_path), "--author", "Alice", "--no-merges", "--count-only"]) == 0
    )
    assert len(seen) == 1
    assert seen[0].author == "Alice"
    assert seen[0].no_merges is True


def test_collect_results_excludes_repositories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    seen: list[str] = []

    monkeypatch.setattr(
        audit,
        "discover_repos",
        lambda *args, **kwargs: {
            "keep-common": str(keep),
            "drop-common": str(drop),
        },
    )

    def fake_pushed(repo: str, filters: Filters) -> dict[str, None]:
        seen.append(repo)
        return {"a" * 40: None}

    monkeypatch.setattr(audit, "pushed_commits_for_repo", fake_pushed)

    filters = Filters(0, 2**31)
    all_commits, per_repo = collect_results(
        [str(tmp_path)],
        filters,
        exclude_patterns=["drop"],
    )
    assert len(all_commits) == 1
    assert len(per_repo) == 1
    assert seen == [str(keep)]
    assert filters.excluded_count == 1


def test_main_exclude_prints_excluded_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    seen: list[str] = []

    monkeypatch.setattr(
        audit,
        "discover_repos",
        lambda *args, **kwargs: {
            "keep-common": str(keep),
            "drop-common": str(drop),
        },
    )

    def fake_pushed(repo: str, filters: Filters) -> dict[str, None]:
        seen.append(repo)
        return {"a" * 40: None}

    monkeypatch.setattr(audit, "pushed_commits_for_repo", fake_pushed)

    assert main([str(tmp_path), "--exclude", "drop"]) == 0

    out = capsys.readouterr().out
    assert "Excluded repos: 1" in out
    assert seen == [str(keep)]


def test_main_exclude_uses_repo_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nexclude = ["drop"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    seen: list[str] = []

    monkeypatch.setattr(
        audit,
        "discover_repos",
        lambda *args, **kwargs: {
            "keep-common": str(keep),
            "drop-common": str(drop),
        },
    )
    monkeypatch.setattr(audit, "load_global_config", lambda: {})

    def fake_pushed(repo: str, filters: Filters) -> dict[str, None]:
        seen.append(repo)
        return {"a" * 40: None}

    monkeypatch.setattr(audit, "pushed_commits_for_repo", fake_pushed)

    assert main([str(tmp_path)]) == 0

    assert "Excluded repos: 1" in capsys.readouterr().out
    assert seen == [str(keep)]

    assert main([str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["excluded"] == 1


def test_main_exclude_cli_wins_over_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nexclude = ["config-excluded"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    cli_excluded = tmp_path / "cli-excluded"
    config_excluded = tmp_path / "config-excluded"
    kept = tmp_path / "kept"
    for repo in (cli_excluded, config_excluded, kept):
        repo.mkdir()
    seen: list[str] = []

    monkeypatch.setattr(
        audit,
        "discover_repos",
        lambda *args, **kwargs: {
            "cli": str(cli_excluded),
            "config": str(config_excluded),
            "kept": str(kept),
        },
    )
    monkeypatch.setattr(audit, "load_global_config", lambda: {})

    def fake_pushed(repo: str, filters: Filters) -> dict[str, None]:
        seen.append(repo)
        return {"a" * 40: None}

    monkeypatch.setattr(audit, "pushed_commits_for_repo", fake_pushed)

    assert main(["--exclude", "cli-excluded", str(tmp_path)]) == 0

    assert "Excluded repos: 1" in capsys.readouterr().out
    assert set(seen) == {str(config_excluded), str(kept)}


def test_main_exclude_prints_zero_when_nothing_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(audit, "collect_results", lambda *args, **kwargs: ({}, []))

    assert main([str(tmp_path), "--exclude", "missing"]) == 0

    assert "Excluded repos: 0" in capsys.readouterr().out


def test_pushed_source_gh_collects_push_event_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 40
    commands: list[list[str]] = []
    event = {
        "type": "PushEvent",
        "created_at": "2026-01-01T00:00:05Z",
        "payload": {
            "commits": [
                {
                    "sha": sha,
                    "distinct": True,
                    "author": {"name": "Alice", "email": "alice@example.com"},
                }
            ]
        },
    }

    monkeypatch.setattr(audit, "_github_repos_for_repo", lambda repo: ["acme/project"])

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([event]),
            stderr="",
        )

    monkeypatch.setattr(audit.subprocess, "run", fake_run)
    filters = Filters(
        since_unix=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
        until_unix=int(datetime(2026, 1, 2, tzinfo=UTC).timestamp()),
    )

    assert audit.pushed_commits_for_repo_gh("/fake/repo", filters) == {sha: None}
    assert commands == [
        ["gh", "api", "--paginate", "repos/acme/project/events?per_page=100"]
    ]


def test_pushed_source_gh_compare_fallback_for_empty_event_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 40
    before = "b" * 40
    head = "c" * 40
    commands: list[list[str]] = []
    event = {
        "type": "PushEvent",
        "created_at": "2026-01-01T00:00:05Z",
        "payload": {
            "before": before,
            "head": head,
            "size": 1,
            "distinct_size": 1,
            "commits": [],
        },
    }
    comparison = {
        "commits": [
            {
                "sha": sha,
                "commit": {"author": {"name": "Alice", "email": "alice@example.com"}},
            }
        ]
    }

    monkeypatch.setattr(audit, "_github_repos_for_repo", lambda repo: ["acme/project"])

    def fake_run(command, **kwargs):
        commands.append(command)
        if "events?per_page=100" in command[-1]:
            response = [event]
        else:
            assert command[-1] == f"repos/acme/project/compare/{before}...{head}"
            response = comparison
        return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)
    filters = Filters(
        since_unix=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()),
        until_unix=int(datetime(2026, 1, 2, tzinfo=UTC).timestamp()),
    )

    assert audit.pushed_commits_for_repo_gh("/fake/repo", filters) == {sha: None}
    assert commands == [
        ["gh", "api", "--paginate", "repos/acme/project/events?per_page=100"],
        ["gh", "api", f"repos/acme/project/compare/{before}...{head}"],
    ]


def test_pushed_source_gh_warns_for_non_github_remote(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(audit, "_github_repos_for_repo", lambda repo: [])
    filters = Filters(since_unix=0, until_unix=2**31)

    assert audit.pushed_commits_for_repo_gh("/fake/repo", filters) == {}
    assert filters.gh_missing_remote_count == 1
    assert "no GitHub remote" in capsys.readouterr().err

    print_report(
        {},
        [],
        "24 hours",
        ["/root"],
        top_n=10,
        source="gh",
        gh_missing_remote_count=filters.gh_missing_remote_count,
    )
    assert "had no GitHub remote" in capsys.readouterr().out


def test_main_source_gh_auth_failure_is_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_collect(*args, **kwargs):
        assert kwargs["source"] == "gh"
        raise audit.GhCollectionError("gh api failed: authentication required")

    monkeypatch.setattr(audit, "collect_results", fake_collect)

    assert main([str(tmp_path), "--source", "gh"]) == 2
    captured = capsys.readouterr()
    assert "ERROR: GitHub source unavailable:" in captured.err
    assert "authentication required" in captured.err
