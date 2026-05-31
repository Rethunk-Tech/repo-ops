#!/usr/bin/env python3
"""Count commits pushed to remotes in a rolling time window across local git repos.

Uses remote-tracking reflog entries marked ``update by push`` (recorded on this
machine when ``git push`` updates refs/remotes/*). Repos are discovered under
configurable roots and deduplicated by ``git rev-parse --git-common-dir``.

Console entry point: ``repo-ops pushed`` (see ``--help`` for flags).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from repo_ops_core.cli import (
    add_json_argument,
    add_roots_argument,
    add_verbose_argument,
    parse_iso8601,
    wire_verbose,
)
from repo_ops_core.discovery import discover_repos
from repo_ops_core.git import repo_label, run_git
from repo_ops_core.table import column_width, format_cell, join_row

PUSH_RE = re.compile(r"^([0-9a-f]{4,40})\s+.+?@\{(\d+)\}:\s+update by push(?:\s|\(|$)")
REFLOG_INDEX_RE = re.compile(r"@\{(\d+)\}")


@dataclass
class Filters:
    """Scan parameters threaded through collect/scan functions."""

    since_unix: int
    until_unix: int
    author: str | None = None
    no_merges: bool = False


@dataclass
class RepoResult:
    label: str
    path: str
    # dict preserves insertion order (chrono from rev-list --reverse) and deduplicates
    commits: dict[str, None] = field(default_factory=dict)


def _reflog_pre_sha(repo: str, ref: str, new_sha: str) -> str | None:
    """Return the OID ``ref`` pointed to immediately before ``new_sha`` was recorded.

    Uses ``git rev-parse ref@{n+1}`` for the reflog entry at ``@{n}`` whose new tip
    is ``new_sha``. Returns ``None`` when there is no prior reflog entry (first push).
    """
    proc = run_git(repo, "reflog", "show", ref)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        tip = parts[0]
        if tip != new_sha and not new_sha.startswith(tip):
            continue
        idx_match = REFLOG_INDEX_RE.search(line)
        if not idx_match:
            continue
        n = int(idx_match.group(1))
        pre = run_git(repo, "rev-parse", f"{ref}@{{{n + 1}}}")
        if pre.returncode != 0 or not pre.stdout.strip():
            return None
        old = pre.stdout.strip()
        if old == new_sha:
            return None
        return old
    return None


def pushed_commits_for_repo(repo: str, filters: Filters) -> dict[str, None]:
    try:
        proc = run_git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/")
    except subprocess.TimeoutExpired:
        print(f"WARN: skipped {repo}: timeout", file=sys.stderr)
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}

    # dict preserves insertion order (chrono from rev-list --reverse) and deduplicates
    commits: dict[str, None] = {}
    refs = [
        line for line in proc.stdout.splitlines() if line and not line.endswith("/HEAD")
    ]

    for ref in refs:
        try:
            rl = run_git(repo, "reflog", "show", ref, "--date=unix")
        except subprocess.TimeoutExpired:
            print(f"WARN: skipped {repo} (ref {ref}): timeout", file=sys.stderr)
            continue
        if rl.returncode != 0 or not rl.stdout.strip():
            continue

        entries: list[tuple[int, str]] = []
        for line in rl.stdout.splitlines():
            match = PUSH_RE.match(line.strip())
            if match:
                entries.append((int(match.group(2)), match.group(1)))

        for i, (ts, new_sha) in enumerate(entries):
            if not (filters.since_unix <= ts < filters.until_unix):
                # Non-monotonic timestamps (clock skew): skip, don't stop
                continue
            if i + 1 < len(entries):
                old_sha = entries[i + 1][1]
            else:
                # Fix 1: oldest reflog entry — resolve pre-state via git log -g
                old_sha = _reflog_pre_sha(repo, ref, new_sha)  # type: ignore[assignment]
                if old_sha is None:
                    # No parent means this was the very first push; list all commits
                    old_sha = "--root"
            if old_sha != "--root" and old_sha == new_sha:
                continue
            try:
                rev_args = ["rev-list", "--reverse"]
                if filters.author:
                    rev_args.append(f"--author={filters.author}")
                if filters.no_merges:
                    rev_args.append("--no-merges")
                if old_sha == "--root":
                    rev_args.append(new_sha)
                else:
                    rev_args.append(f"{old_sha}..{new_sha}")
                rev = run_git(repo, *rev_args)
            except subprocess.TimeoutExpired:
                print(f"WARN: skipped {repo} (rev-list): timeout", file=sys.stderr)
                continue
            if rev.returncode != 0:
                continue
            for commit in rev.stdout.splitlines():
                commit = commit.strip()
                if commit:
                    commits[commit] = None
    return commits


def collect_results(
    roots: list[str], filters: Filters
) -> tuple[dict[str, None], list[RepoResult]]:
    common_to_repo = discover_repos(roots)
    per_repo: list[RepoResult] = []
    all_commits: dict[str, None] = {}

    max_workers = min(16, (os.cpu_count() or 4))
    repos_sorted = sorted(common_to_repo.values())

    def _scan_repo(repo: str) -> RepoResult | None:
        commits = pushed_commits_for_repo(repo, filters)
        if not commits:
            return None
        return RepoResult(
            label=repo_label(repo, roots),
            path=repo,
            commits=commits,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_repo, repo): repo for repo in repos_sorted}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                per_repo.append(result)
                all_commits.update(result.commits)

    per_repo.sort(key=lambda r: (-len(r.commits), r.label))
    return all_commits, per_repo


def resolve_subjects(per_repo: list[RepoResult]) -> dict[str, dict[str, str]]:
    """Return {repo_path: {sha: subject}} for all repos with commits.

    Subjects are truncated to 100 chars with a single ellipsis character.
    """
    result: dict[str, dict[str, str]] = {}
    for r in per_repo:
        subs: dict[str, str] = {}
        for sha in r.commits:
            proc = run_git(r.path, "log", "-1", "--format=%s", sha)
            subject = proc.stdout.strip() if proc.returncode == 0 else ""
            if len(subject) > 100:
                subject = subject[:100] + "…"
            subs[sha] = subject
        result[r.path] = subs
    return result


def print_report(
    all_commits: dict[str, None],
    per_repo: list[RepoResult],
    window_desc: str,
    roots: list[str],
    top_n: int,
    show_shas: bool = False,
    show_messages: bool = False,
) -> None:
    print(f"Pushed commits in the last {window_desc} (local reflog: update by push)")
    print(f"Scanned {len(roots)} root(s); {len(per_repo)} repo(s) with pushes")
    print()
    print(f"Total unique commits pushed: {len(all_commits)}")
    print()

    if not per_repo:
        print("No pushes recorded in the window.")
        return

    shown = per_repo[:top_n]
    labels = [r.label for r in shown]
    commit_counts = [str(len(r.commits)) for r in shown]
    if len(per_repo) > top_n:
        other = sum(len(r.commits) for r in per_repo[top_n:])
        commit_counts.append(str(other))
    commits_w = max(7, column_width(commit_counts, "commits"))
    label_w = column_width(labels, "repo")
    print(f"Top {min(top_n, len(per_repo))} repos by pushed commits:")
    header = join_row(
        [
            format_cell("commits", commits_w, align="right"),
            format_cell("repo", label_w),
        ]
    )
    print(header)
    print("-" * len(header))
    for row in shown:
        print(
            join_row(
                [
                    format_cell(str(len(row.commits)), commits_w, align="right"),
                    format_cell(row.label, label_w),
                ]
            )
        )
    if len(per_repo) > top_n:
        print(
            join_row(
                [
                    format_cell(str(other), commits_w, align="right"),
                    format_cell(
                        f"({len(per_repo) - top_n} other repos)",
                        label_w,
                    ),
                ]
            )
        )

    if not show_shas:
        return

    subjects: dict[str, dict[str, str]] = {}
    if show_messages:
        subjects = resolve_subjects(per_repo)

    print()
    print("Commits by repo:")
    for r in per_repo:
        if not r.commits:
            continue
        print(f"\n  {r.label}")
        for sha in r.commits:
            short = sha[:8]
            if show_messages:
                subj = subjects.get(r.path, {}).get(sha, "")
                print(f"    {short}  {subj}")
            else:
                print(f"    {short}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repo-ops pushed",
        description="Count commits pushed in a rolling window across local git repos.",
    )
    add_roots_argument(parser)

    def positive_hours(value: str) -> float:
        v = float(value)
        if v <= 0:
            raise argparse.ArgumentTypeError(f"--hours must be > 0, got {value!r}")
        return v

    parser.add_argument(
        "--hours",
        type=positive_hours,
        default=None,
        help="Rolling window length in hours (default: 24; must be > 0)",
    )
    parser.add_argument(
        "--since",
        type=parse_iso8601,
        metavar="ISO8601",
        default=None,
        help="Window start (ISO 8601, timezone required, e.g. 2026-05-26T00:00Z)",
    )
    parser.add_argument(
        "--until",
        type=parse_iso8601,
        metavar="ISO8601",
        default=None,
        help="Window end (ISO 8601, timezone required); defaults to now",
    )
    parser.add_argument(
        "--author",
        default=None,
        metavar="PATTERN",
        help="Passed as --author=<pattern> to git rev-list; empty string rejected",
    )
    parser.add_argument(
        "--no-merges",
        action="store_true",
        help="Exclude merge commits (passed to git rev-list)",
    )
    parser.add_argument(
        "--show-shas",
        action="store_true",
        help="After the table, print each commit SHA (8 chars) grouped by repo",
    )
    parser.add_argument(
        "--show-messages",
        action="store_true",
        help="Implies --show-shas; also print each commit subject (max 100 chars)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top repos to show (default: 10)",
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only the total commit count",
    )
    add_json_argument(parser, dest="json_out")
    add_verbose_argument(parser)
    args = parser.parse_args(argv)
    wire_verbose(args)

    # --show-messages implies --show-shas; set once here to avoid repeated checks
    if args.show_messages:
        args.show_shas = True

    # Reject empty --author
    if args.author is not None and args.author == "":
        parser.error("--author value must not be empty")

    # Resolve time window
    now = datetime.now(timezone.utc)
    effective_hours = args.hours if args.hours is not None else 24.0

    if args.since is not None and args.until is not None:
        # Both provided: explicit window; --hours is ignored
        since_dt = args.since
        until_dt = args.until
    elif args.since is not None:
        # --since only: window = [since, now)
        if args.hours is not None:
            print(
                "WARNING: --hours is ignored because --since is provided without --until",
                file=sys.stderr,
            )
        since_dt = args.since
        until_dt = now
    elif args.until is not None:
        # --until only: window = [now - hours, until)
        since_dt = now - timedelta(hours=effective_hours)
        until_dt = args.until
    else:
        # Neither: rolling hours window
        since_dt = now - timedelta(hours=effective_hours)
        until_dt = now

    if since_dt >= until_dt:
        parser.error(
            f"--since ({since_dt.isoformat()}) must be before --until ({until_dt.isoformat()})"
        )

    since_unix = int(since_dt.timestamp())
    until_unix = int(until_dt.timestamp())

    filters = Filters(
        since_unix=since_unix,
        until_unix=until_unix,
        author=args.author,
        no_merges=args.no_merges,
    )

    # Build a human-readable window description for the report header
    if args.since is not None or args.until is not None:
        window_desc = f"{since_dt.isoformat()} to {until_dt.isoformat()}"
    else:
        window_desc = f"{effective_hours:g} hour" + (
            "" if effective_hours == 1 else "s"
        )

    roots = list(args.roots)
    all_commits, per_repo = collect_results(roots, filters)

    if args.json_out:
        # Resolve subjects before serialising when --show-messages is set
        subjects: dict[str, dict[str, str]] = {}
        if args.show_messages:
            subjects = resolve_subjects(per_repo)

        def _repo_entry(r: RepoResult) -> dict:
            entry: dict = {
                "repo": r.label,
                "path": r.path,
                "commits": len(r.commits),
            }
            if args.show_shas:
                sha_list = list(r.commits.keys())
                entry["shas"] = sha_list
            if args.show_messages:
                repo_subs = subjects.get(r.path, {})
                entry["commits_detail"] = [
                    {"sha": sha, "subject": repo_subs.get(sha, "")} for sha in r.commits
                ]
            return entry

        payload = {
            "window": {
                "since": since_dt.isoformat(),
                "until": until_dt.isoformat(),
            },
            "roots": roots,
            "total": len(all_commits),
            "repos_with_pushes": len(per_repo),
            # Consumers slice from "repos" themselves; "top" key removed.
            "repos": [_repo_entry(r) for r in per_repo],
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.count_only:
        print(len(all_commits))
        return 0

    print_report(
        all_commits,
        per_repo,
        window_desc,
        roots,
        args.top,
        show_shas=args.show_shas,
        show_messages=args.show_messages,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
