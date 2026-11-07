"""Skip local clones of archived GitHub repos: nothing there can ever be pushed."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_core.git import github_owner_repo, github_remote, run_git

# Maps an org or user to its archived "owner/name" slugs (lowercase), or None
# when the archive list could not be fetched (gh missing or unauthenticated).
ArchiveLister = Callable[[str], set[str] | None]


@dataclass
class ArchiveScan:
    archived: dict[Path, int] = field(default_factory=dict)
    """Archived clone to its count of commits not on any remote."""
    unchecked_orgs: list[str] = field(default_factory=list)


def gh_archived_lister(org: str) -> set[str] | None:
    try:
        proc = subprocess.run(
            ["gh", "repo", "list", org, "--archived", "--limit", "1000", "--json", "nameWithOwner"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return {row["nameWithOwner"].lower() for row in json.loads(proc.stdout)}
    except (ValueError, KeyError, TypeError):
        return None


def unpushed_commits(repo: Path) -> int:
    proc = run_git(repo, "rev-list", "--count", "HEAD", "--not", "--remotes")
    return int(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else 0


def scan_archived(repos: list[Path], lister: ArchiveLister = gh_archived_lister) -> ArchiveScan:
    """Find which of *repos* (git roots) have an archived GitHub origin.

    One lister call per distinct org, however many clones share it.
    """
    scan = ArchiveScan()
    by_org: dict[str, list[tuple[Path, str]]] = {}
    for repo in repos:
        remote = github_remote(repo)
        slug = github_owner_repo(remote) if remote else None
        if slug:
            by_org.setdefault(slug.split("/", 1)[0], []).append((repo, slug.lower()))
    for org, members in by_org.items():
        archived = lister(org)
        if archived is None:
            scan.unchecked_orgs.append(org)
            continue
        for repo, slug in members:
            if slug in archived:
                scan.archived[repo] = unpushed_commits(repo)
    return scan


def archived_message(repo: Path, unpushed: int) -> str:
    return f"{repo}: archived upstream: delete the local clone ({unpushed} unpushed commits)"


def print_archive_report(scan: ArchiveScan, *, checked: int) -> None:
    print(
        f"archive check: {checked} repo(s) scanned, {len(scan.archived)} archived, "
        f"{len(scan.unchecked_orgs)} org(s) unchecked"
    )
    if scan.unchecked_orgs:
        print(
            "archive check could not run for: "
            + ", ".join(sorted(scan.unchecked_orgs))
            + " (gh missing, unauthenticated or failing); archived clones there are not skipped"
        )
    if scan.archived:
        print("archived upstream: delete the local clone")
        for repo, unpushed in sorted(scan.archived.items()):
            print(f"  {repo} ({unpushed} unpushed commits)")
