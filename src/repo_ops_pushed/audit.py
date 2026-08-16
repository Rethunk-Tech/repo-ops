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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from repo_ops_core.cli import (
    add_exclude_argument,
    add_follow_symlinks_argument,
    add_json_argument,
    add_json_out_argument,
    add_roots_argument,
    add_verbose_argument,
    parse_iso8601,
    positive_int,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.config import load_global_config, load_repo_config
from repo_ops_core.discovery import discover_repos
from repo_ops_core.exclude import filter_repo_paths, merge_exclude_patterns
from repo_ops_core.filesystem import write_json_atomic
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
    excluded_count: int = 0
    gh_missing_remote_count: int = 0


@dataclass
class RepoResult:
    label: str
    path: str
    # dict preserves insertion order (chrono from rev-list --reverse) and deduplicates
    commits: dict[str, None] = field(default_factory=dict)


class GhCollectionError(RuntimeError):
    """Raised when the GitHub source cannot produce a trustworthy result."""


def _github_repo_slug(remote_url: str) -> str | None:
    """Return ``owner/repo`` for a github.com remote URL."""
    value = remote_url.strip()
    if value.startswith("git@"):
        value = "ssh://" + value.replace(":", "/", 1)
    parsed = urlparse(value)
    if parsed.hostname != "github.com":
        return None
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if path.count("/") != 1 or any(not part for part in path.split("/")):
        return None
    return path


def _github_repos_for_repo(repo: str) -> list[str]:
    try:
        remotes = run_git(repo, "remote")
    except subprocess.TimeoutExpired as exc:
        raise GhCollectionError(f"reading remotes for {repo} timed out") from exc
    if remotes.returncode != 0:
        detail = remotes.stderr.strip() if hasattr(remotes, "stderr") else ""
        raise GhCollectionError(
            f"reading remotes for {repo} failed" + (f": {detail}" if detail else "")
        )

    slugs: set[str] = set()
    for remote in remotes.stdout.splitlines():
        remote = remote.strip()
        if not remote:
            continue
        try:
            urls = run_git(repo, "remote", "get-url", "--all", remote)
        except subprocess.TimeoutExpired as exc:
            raise GhCollectionError(f"reading remote {remote} for {repo} timed out") from exc
        if urls.returncode != 0:
            detail = urls.stderr.strip() if hasattr(urls, "stderr") else ""
            raise GhCollectionError(
                f"reading remote {remote} for {repo} failed" + (f": {detail}" if detail else "")
            )
        for url in urls.stdout.splitlines():
            slug = _github_repo_slug(url)
            if slug is not None:
                slugs.add(slug)
    return sorted(slugs)


def _decode_json_values(raw: str, endpoint: str) -> list[object]:
    decoder = json.JSONDecoder()
    values: list[object] = []
    offset = 0
    while offset < len(raw):
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        if offset >= len(raw):
            break
        try:
            value, end = decoder.raw_decode(raw, offset)
        except json.JSONDecodeError as exc:
            raise GhCollectionError(f"gh api {endpoint} returned invalid JSON") from exc
        values.append(value)
        offset = end
    return values


def _gh_api(endpoint: str, *, paginate: bool = False) -> object:
    command = ["gh", "api"]
    if paginate:
        command.append("--paginate")
    command.append(endpoint)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise GhCollectionError("gh CLI is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GhCollectionError(f"gh api {endpoint} timed out") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise GhCollectionError(
            f"gh api {endpoint} failed"
            + (f": {detail}" if detail else f" (exit {proc.returncode})")
        )

    values = _decode_json_values(proc.stdout, endpoint)
    if not values:
        raise GhCollectionError(f"gh api {endpoint} returned no JSON")
    if len(values) == 1:
        return values[0]
    return values


def _github_events(endpoint: str) -> list[dict]:
    response = _gh_api(endpoint, paginate=True)
    pages = response if isinstance(response, list) else [response]
    events: list[dict] = []
    for page in pages:
        page_events = page if isinstance(page, list) else [page]
        for event in page_events:
            if not isinstance(event, dict):
                raise GhCollectionError(f"gh api {endpoint} returned invalid events")
            events.append(event)
    return events


def _event_unix(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp())


def _github_commit_matches(
    repo_slug: str,
    sha: str,
    commit: dict,
    filters: Filters,
) -> bool:
    if filters.author:
        author = commit.get("author")
        if not isinstance(author, dict):
            commit_details = commit.get("commit")
            if isinstance(commit_details, dict):
                author = commit_details.get("author")
        if not isinstance(author, dict):
            return False
        identity = f"{author.get('name', '')} <{author.get('email', '')}>"
        try:
            if re.search(filters.author, identity) is None:
                return False
        except re.error:
            return False
    if not filters.no_merges:
        return True

    detail = _gh_api(f"repos/{repo_slug}/commits/{sha}")
    if not isinstance(detail, dict):
        raise GhCollectionError(f"gh api repos/{repo_slug}/commits/{sha} returned invalid commit")
    parents = detail.get("parents")
    return not isinstance(parents, list) or len(parents) < 2


def _github_compare_commits(repo_slug: str, payload: dict) -> list[dict]:
    before = payload.get("before")
    head = payload.get("head")
    if (
        not isinstance(before, str)
        or not isinstance(head, str)
        or re.fullmatch(r"[0-9a-fA-F]{40}", before) is None
        or re.fullmatch(r"[0-9a-fA-F]{40}", head) is None
        or before == "0" * 40
        or before == head
    ):
        return []

    size = payload.get("distinct_size", payload.get("size"))
    if isinstance(size, int) and size <= 0:
        return []

    endpoint = f"repos/{repo_slug}/compare/{before}...{head}"
    response = _gh_api(endpoint)
    if not isinstance(response, dict):
        raise GhCollectionError(f"gh api {endpoint} returned invalid comparison")
    records = response.get("commits")
    if not isinstance(records, list):
        raise GhCollectionError(f"gh api {endpoint} returned no commits list")
    return [record for record in records if isinstance(record, dict)]


def _add_github_commits(
    commits: dict[str, None],
    repo_slug: str,
    records: list[dict],
    filters: Filters,
    *,
    distinct_only: bool,
) -> None:
    for record in records:
        sha = record.get("sha")
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
            continue
        if distinct_only and record.get("distinct", True) is False:
            continue
        if _github_commit_matches(repo_slug, sha, record, filters):
            commits[sha] = None


def pushed_commits_for_repo_gh(repo: str, filters: Filters) -> dict[str, None]:
    commits: dict[str, None] = {}
    repo_slugs = _github_repos_for_repo(repo)
    if not repo_slugs:
        filters.gh_missing_remote_count += 1
        print(
            f"WARN: skipped {repo} for --source=gh: no GitHub remote",
            file=sys.stderr,
        )
        return commits
    for repo_slug in repo_slugs:
        endpoint = f"repos/{repo_slug}/events?per_page=100"
        for event in _github_events(endpoint):
            if event.get("type") != "PushEvent":
                continue
            timestamp = _event_unix(event.get("created_at"))
            if timestamp is None or not (filters.since_unix <= timestamp < filters.until_unix):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            event_commits = payload.get("commits")
            if isinstance(event_commits, list) and event_commits:
                _add_github_commits(
                    commits,
                    repo_slug,
                    [record for record in event_commits if isinstance(record, dict)],
                    filters,
                    distinct_only=True,
                )
                continue
            compare_commits = _github_compare_commits(repo_slug, payload)
            _add_github_commits(
                commits,
                repo_slug,
                compare_commits,
                filters,
                distinct_only=False,
            )
    return commits


def _reflog_pre_sha(repo: str, ref: str, new_sha: str, reflog_lines: list[str]) -> str | None:
    """Return the OID ``ref`` pointed to immediately before ``new_sha`` was recorded.

    Uses ``git rev-parse ref@{n+1}`` for the reflog entry at ``@{n}`` whose new tip
    is ``new_sha``. Returns ``None`` when there is no prior reflog entry (first push).

    ``reflog_lines`` must be the output of ``git reflog show <ref>`` **without**
    ``--date=unix`` so that ``@{n}`` carries the sequential index git uses for
    ``ref@{n+1}`` resolution.
    """
    for line in reflog_lines:
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
        try:
            pre = run_git(repo, "rev-parse", f"{ref}@{{{n + 1}}}")
        except subprocess.TimeoutExpired:
            print(f"WARN: skipped {repo} (ref {ref}): timeout", file=sys.stderr)
            return None
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
    refs = [line for line in proc.stdout.splitlines() if line and not line.endswith("/HEAD")]

    for ref in refs:
        try:
            rl = run_git(repo, "reflog", "show", ref, "--date=unix")
        except subprocess.TimeoutExpired:
            print(f"WARN: skipped {repo} (ref {ref}): timeout", file=sys.stderr)
            continue
        if rl.returncode != 0 or not rl.stdout.strip():
            continue

        # Fetch the plain reflog (no --date=unix) once per ref so that _reflog_pre_sha
        # can use the sequential @{n} indices for ref@{n+1} resolution.  This replaces
        # the per-push-entry subprocess that _reflog_pre_sha previously spawned itself,
        # reducing from O(pushes x refs) to O(refs) reflog subprocess calls.
        try:
            rl_plain = run_git(repo, "reflog", "show", ref)
        except subprocess.TimeoutExpired:
            print(f"WARN: skipped {repo} (ref {ref}): timeout", file=sys.stderr)
            continue
        if rl_plain.returncode != 0:
            continue
        plain_lines: list[str] = rl_plain.stdout.splitlines()

        entries: list[tuple[int, str]] = []
        for line in rl.stdout.splitlines():
            match = PUSH_RE.match(line.strip())
            if match:
                entries.append((int(match.group(2)), match.group(1)))

        for ts, new_sha in entries:
            if not (filters.since_unix <= ts < filters.until_unix):
                # Non-monotonic timestamps (clock skew): skip, don't stop
                continue
            # Fix #92: use _reflog_pre_sha for ALL entries, not just the oldest.
            # entries[i+1][1] (previous push tip) is wrong when a git fetch advanced
            # the remote-tracking ref between two pushes — the fetched commits would
            # be included in the range and miscounted as this push's commits.
            # _reflog_pre_sha walks the full reflog to find ref@{n+1} for the exact
            # entry where the tip matched new_sha, returning the true pre-push state.
            old_sha = _reflog_pre_sha(repo, ref, new_sha, plain_lines)
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
    roots: list[str],
    filters: Filters,
    *,
    follow_symlinks: bool = False,
    jobs: int | None = None,
    exclude_patterns: list[str] | None = None,
    source: str = "local",
) -> tuple[dict[str, None], list[RepoResult]]:
    common_to_repo = discover_repos(roots, follow_symlinks=follow_symlinks)
    per_repo: list[RepoResult] = []
    all_commits: dict[str, None] = {}

    max_workers = jobs if jobs is not None else min(16, (os.cpu_count() or 4))
    repo_roots = list(roots)
    kept_paths, filters.excluded_count = filter_repo_paths(
        [Path(repo) for repo in common_to_repo.values()],
        exclude_patterns or [],
        label_fn=lambda path: repo_label(str(path), repo_roots),
    )
    kept_set = set(kept_paths)
    repos_sorted = sorted(repo for repo in common_to_repo.values() if Path(repo) in kept_set)

    scanner = pushed_commits_for_repo
    if source == "gh":
        scanner = pushed_commits_for_repo_gh

    def _scan_repo(repo: str) -> RepoResult | None:
        commits = scanner(repo, filters)
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
            try:
                proc = run_git(r.path, "log", "-1", "--format=%s", sha)
                subject = proc.stdout.strip() if proc.returncode == 0 else ""
            except subprocess.TimeoutExpired:
                print(f"WARN: skipped {r.path} (log {sha[:8]}): timeout", file=sys.stderr)
                subject = ""
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
    excluded_count: int = 0,
    show_excluded: bool = False,
    source: str = "local",
    gh_missing_remote_count: int = 0,
) -> None:
    if source == "gh":
        print(f"Pushed commits in the last {window_desc} (GitHub PushEvent API)")
    else:
        print(f"Pushed commits in the last {window_desc} (local reflog: update by push)")
    if source == "gh" and gh_missing_remote_count:
        print(
            f"WARNING: {gh_missing_remote_count} repo(s) had no GitHub remote; see stderr warnings"
        )
    print(f"Scanned {len(roots)} root(s); {len(per_repo)} repo(s) with pushes")
    if show_excluded:
        print(f"Excluded repos: {excluded_count}")
    print()
    print(f"Total unique commits pushed: {len(all_commits)}")
    print()

    if not per_repo:
        print("No pushes recorded in the window.")
        return

    shown = per_repo[:top_n]
    labels = [r.label for r in shown]
    commit_counts = [str(len(r.commits)) for r in shown]
    other = 0
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


def build_pushed_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops pushed",
        description="Count commits pushed in a rolling window across local git repos.",
    )
    _add_pushed_arguments(parser)
    return parser


def _add_pushed_arguments(parser: argparse.ArgumentParser) -> None:
    add_roots_argument(parser)
    add_exclude_argument(parser)
    add_follow_symlinks_argument(parser)

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
        "--source",
        choices=("local", "gh"),
        default="local",
        help="Push source: local reflogs (default) or authenticated GitHub events",
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
        type=positive_int,
        default=10,
        help="Number of top repos to show (default: 10)",
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only the total commit count",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=None,
        help="Max parallel workers for repo scanning (default: min(16, cpu count)).",
    )
    add_json_argument(parser, dest="json_out")
    add_json_out_argument(parser)
    add_verbose_argument(parser)


def main(argv: list[str] | None = None) -> int:
    parser = build_pushed_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)
    if getattr(args, "json_report_path", None) and not args.json_out:
        parser.error("--json-out requires --json")

    global_cfg = load_global_config()
    repo_cfg = load_repo_config(Path.cwd())
    config_exclude: object = None
    if repo_cfg and "exclude" in repo_cfg:
        config_exclude = repo_cfg["exclude"]
    elif global_cfg and "exclude" in global_cfg:
        config_exclude = global_cfg["exclude"]
    exclude_patterns = merge_exclude_patterns(
        getattr(args, "exclude_patterns", None),
        config_exclude,
    )

    # --show-messages implies --show-shas; set once here to avoid repeated checks
    if args.show_messages:
        args.show_shas = True

    # Reject empty --author
    if args.author is not None and args.author == "":
        parser.error("--author value must not be empty")

    # Resolve time window
    now = datetime.now(UTC)
    effective_hours = args.hours if args.hours is not None else 24.0

    if args.since is not None and args.until is not None:
        # Both provided: explicit window; --hours is ignored
        if args.hours is not None:
            print(
                "WARNING: --hours is ignored because --since and --until are both provided",
                file=sys.stderr,
            )
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
        window_desc = f"{effective_hours:g} hour" + ("" if effective_hours == 1 else "s")

    roots = resolve_scan_roots(list(args.roots), parser)
    follow_symlinks = bool(getattr(args, "follow_symlinks", False))
    collect_kwargs = {
        "follow_symlinks": follow_symlinks,
        "jobs": args.jobs,
        "exclude_patterns": exclude_patterns,
    }
    if args.source == "gh":
        collect_kwargs["source"] = "gh"
    try:
        all_commits, per_repo = collect_results(roots, filters, **collect_kwargs)
    except GhCollectionError as exc:
        print(f"ERROR: GitHub source unavailable: {exc}", file=sys.stderr)
        return 2

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

        total = len(all_commits)
        repos_with_pushes = len(per_repo)
        payload: dict[str, object] = {
            "schema_version": 1,
            "window": {
                "since": since_dt.isoformat(),
                "until": until_dt.isoformat(),
            },
            "roots": roots,
            "total": total,
            "repos_with_pushes": repos_with_pushes,
            # ``summary`` mirrors human footer roll-up (docs/usage.md JSON contract).
            "summary": {
                "total": total,
                "repos_with_pushes": repos_with_pushes,
                "excluded": filters.excluded_count,
            },
            # Consumers slice from "repos" themselves; "top" key removed.
            "repos": [_repo_entry(r) for r in per_repo],
        }
        if args.source == "gh":
            payload["source"] = "gh"
        json_report_path = getattr(args, "json_report_path", None)
        if json_report_path:
            written = write_json_atomic(Path(json_report_path), payload, compact=args.compact)
            print(written)
        else:
            print(json.dumps(payload, indent=None if args.compact else 2))
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
        excluded_count=filters.excluded_count,
        show_excluded=bool(exclude_patterns),
        source=args.source,
        gh_missing_remote_count=filters.gh_missing_remote_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
