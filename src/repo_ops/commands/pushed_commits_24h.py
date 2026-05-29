#!/usr/bin/env python3
"""Count commits pushed to remotes in a rolling time window across local git repos.

Uses remote-tracking reflog entries marked ``update by push`` (recorded on this
machine when ``git push`` updates refs/remotes/*). Repos are discovered under
configurable roots and deduplicated by ``git rev-parse --git-common-dir``.

Usage:
  pushed_commits_24h.py [root_dir ...]
  pushed_commits_24h.py --hours 48
  pushed_commits_24h.py --since 2026-05-26T00:00Z --until 2026-05-27T00:00Z
  pushed_commits_24h.py --count-only
  pushed_commits_24h.py --json
  pushed_commits_24h.py --show-shas
  pushed_commits_24h.py --show-messages

Default roots:
  /usr/local/src
  ~/Projects
  ~/Documents
  ~/bastion-ai-helpers
  ~/Apps
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
from pathlib import Path

from repo_ops.constants import DEFAULT_ROOTS
from repo_ops.filesystem import should_exclude
from repo_ops.git import git_common_dir, repo_label, run_git

PUSH_RE = re.compile(r"^([0-9a-f]{4,40})\s+.+?@\{(\d+)\}:\s+update by push(?:\s|\(|$)")


def parse_iso8601(value: str) -> datetime:
    """Parse an ISO 8601 string into a timezone-aware UTC datetime.

    Replaces trailing 'Z' with '+00:00' for Python < 3.11 compatibility
    (fromisoformat only accepts 'Z' natively from 3.11+).
    Rejects naive datetimes with an argparse-compatible error.
    """
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid ISO 8601 datetime {value!r}: {exc}"
        ) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            f"Datetime {value!r} has no timezone; use e.g. 2026-05-26T00:00Z or +00:00 suffix"
        )
    return dt.astimezone(timezone.utc)


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


def discover_repos(roots: list[str]) -> dict[str, str]:
    """Map git-common-dir -> representative working tree path.

    -L tells find to follow symlinks so that symlinked .git dirs are not missed.
    Both sides of the dedup key are os.path.realpath-normalised so that a
    symlinked repo and its canonical path map to the same key.
    """
    common_to_repo: dict[str, str] = {}
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        proc = subprocess.run(
            [
                "find",
                "-L",
                str(root_path),
                "(",
                "-name",
                "node_modules",
                "-o",
                "-name",
                "vendor",
                "-o",
                "-name",
                "target",
                "-o",
                "-name",
                ".cargo",
                "-o",
                "-name",
                "pkg",
                "-o",
                "-name",
                ".cache",
                "-o",
                "-name",
                ".oh-my-zsh",
                "-o",
                "-name",
                ".oh-my-bash",
                ")",
                "-prune",
                "-o",
                "-type",
                "d",
                "-name",
                ".git",
                "-print",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            continue
        for git_dir in proc.stdout.splitlines():
            if not git_dir:
                continue
            # realpath normalises symlinks so dedup keys match on both sides
            repo = os.path.realpath(os.path.dirname(git_dir))
            if should_exclude(repo):
                continue
            common = git_common_dir(repo)
            if not common:
                continue
            prev = common_to_repo.get(common)
            if prev is None or len(repo) < len(prev):
                common_to_repo[common] = repo
    return common_to_repo


def _reflog_pre_sha(repo: str, ref: str, new_sha: str) -> str | None:
    """Return the SHA that ``ref`` pointed to immediately before ``new_sha``.

    Uses ``git log -g`` which emits the *parent* (pre-state) SHA directly via
    ``%P``, avoiding the off-by-one boundary problem of walking reflog pairs.
    Falls back to the empty-tree SHA so the caller can still rev-list from the
    very beginning of the branch.
    """
    # git log -g --format="%H %P" walks the reflog and gives us both old+new
    # %H = new (post-push) SHA, %P = parent in the reflog (pre-push SHA)
    proc = run_git(repo, "log", "-g", "--format=%H %P", ref)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 1 and parts[0] == new_sha:
            return parts[1] if len(parts) >= 2 else None
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

    label_w = max(len(r.label) for r in per_repo[:top_n])
    label_w = max(label_w, len("repo"))
    print(f"Top {min(top_n, len(per_repo))} repos by pushed commits:")
    print(f"{'commits'.rjust(7)}  {'repo'.ljust(label_w)}")
    print("-" * (7 + 2 + label_w))
    for row in per_repo[:top_n]:
        print(f"{len(row.commits):>7}  {row.label.ljust(label_w)}")
    if len(per_repo) > top_n:
        other = sum(len(r.commits) for r in per_repo[top_n:])
        print(f"{other:>7}  ({len(per_repo) - top_n} other repos)")

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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count commits pushed in a rolling window across local git repos.",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        help="Directories to scan for git repositories (default: standard local roots)",
    )

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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    args = parser.parse_args()

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

    roots = args.roots or DEFAULT_ROOTS
    all_commits, per_repo = collect_results(roots, filters)

    if args.json:
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
