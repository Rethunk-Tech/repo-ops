"""Per-repo git status: branch, dirty file count, and ahead/behind vs upstream.

``git status --porcelain=v1 --branch -z`` answers all four in one subprocess:
the branch header carries the upstream and its ahead/behind counts, and the
remaining records are the dirty entries. Repos whose branch has no upstream
configured fall back to ``origin/HEAD`` so a fresh clone still reports drift.

Counts are measured against remote-tracking refs as they stand on disk, so a
report is only as current as the last fetch (see ``fetch_repo``).
"""

from __future__ import annotations

import concurrent.futures
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repo_ops_core.git import repo_label, run_git
from repo_ops_core.parallel import run_bounded_pool

#: Rename and copy records in ``--porcelain -z`` are followed by a second path
#: field (the origin), which must not be counted as another dirty entry.
_TWO_PATH_STATUS = frozenset({"R", "C"})

_BRANCH_HEADER = re.compile(
    r"^## (?P<branch>[^.\s]+(?:\.[^.\s]+)*?)(?:\.\.\.(?P<upstream>\S+))?"
    r"(?: \[(?P<track>[^\]]*)\])?$"
)
_NO_COMMITS = re.compile(r"^## No commits yet on (?P<branch>.+)$")
_AHEAD = re.compile(r"ahead (\d+)")
_BEHIND = re.compile(r"behind (\d+)")

DETACHED = "(detached)"


@dataclass(frozen=True)
class RepoStatus:
    """One row of the status report.

    ``ahead``/``behind`` are None when no comparison ref exists (no upstream and
    no ``origin/HEAD``), which is distinct from a measured zero.
    """

    label: str
    path: str
    branch: str
    upstream: str | None
    dirty: int
    ahead: int | None
    behind: int | None
    empty: bool = False
    error: str | None = None

    @property
    def comparable(self) -> bool:
        return self.ahead is not None and self.behind is not None


def _parse_porcelain_z(payload: str) -> tuple[str, str | None, int | None, int | None, int, bool]:
    """Return ``(branch, upstream, ahead, behind, dirty, empty)`` from ``-z`` output."""
    records = [r for r in payload.split("\0") if r]
    if not records:
        return DETACHED, None, None, None, 0, False

    header, entries = records[0], records[1:]
    branch, upstream, ahead, behind = DETACHED, None, None, None
    empty = False

    if (no_commits := _NO_COMMITS.match(header)) is not None:
        branch = no_commits.group("branch")
        empty = True
    elif header == "## HEAD (no branch)":
        branch = DETACHED
    elif (matched := _BRANCH_HEADER.match(header)) is not None:
        branch = matched.group("branch")
        upstream = matched.group("upstream")
        track = matched.group("track") or ""
        if upstream is not None and track != "gone":
            ahead = int(m.group(1)) if (m := _AHEAD.search(track)) else 0
            behind = int(m.group(1)) if (m := _BEHIND.search(track)) else 0

    dirty = 0
    skip_next = False
    for entry in entries:
        if skip_next:
            skip_next = False
            continue
        dirty += 1
        # "XY path" — a rename/copy adds the origin path as its own record.
        if len(entry) >= 2 and (entry[0] in _TWO_PATH_STATUS or entry[1] in _TWO_PATH_STATUS):
            skip_next = True
    return branch, upstream, ahead, behind, dirty, empty


def _fallback_upstream(repo: str) -> str | None:
    """Resolve ``origin/HEAD`` for a branch with no configured upstream."""
    proc = run_git(repo, "rev-parse", "--abbrev-ref", "--verify", "origin/HEAD")
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    return ref or None


def _count_against(repo: str, upstream: str) -> tuple[int, int] | None:
    """Return ``(ahead, behind)`` for HEAD vs *upstream*, or None if unmeasurable."""
    proc = run_git(repo, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    if proc.returncode != 0:
        return None
    parts = proc.stdout.split()
    if len(parts) != 2:
        return None
    behind, ahead = parts
    return int(ahead), int(behind)


def collect_status(repo: str, label: str) -> RepoStatus:
    """Collect branch, dirty count, and ahead/behind for a single repo."""
    try:
        proc = run_git(repo, "status", "--porcelain=v1", "--branch", "-z")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return RepoStatus(label, repo, DETACHED, None, 0, None, None, error=str(exc))
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        return RepoStatus(
            label,
            repo,
            DETACHED,
            None,
            0,
            None,
            None,
            error=detail[0] if detail else f"git status exited {proc.returncode}",
        )

    branch, upstream, ahead, behind, dirty, empty = _parse_porcelain_z(proc.stdout)

    if ahead is None and not empty and branch != DETACHED:
        # No configured upstream (or a gone one): compare against origin/HEAD so
        # clones that were never `--set-upstream`ed still report drift. A detached
        # HEAD is excluded deliberately — a submodule parked at its parent's pin
        # is where it belongs, and scoring it against origin/HEAD would report
        # every pin that upstream has moved past as if the checkout had drifted.
        fallback = _fallback_upstream(repo)
        if fallback is not None and (counts := _count_against(repo, fallback)) is not None:
            upstream = fallback
            ahead, behind = counts

    return RepoStatus(label, repo, branch, upstream, dirty, ahead, behind, empty=empty)


def fetch_repo(repo: str, *, timeout: float = 120.0) -> bool:
    """Refresh remote-tracking refs; False when the fetch failed or timed out."""
    try:
        proc = run_git(repo, "fetch", "--all", "--prune", "--quiet", timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return proc.returncode == 0


def collect_all(
    repos: list[tuple[str, str]],
    *,
    jobs: int,
    fetch: bool = False,
) -> tuple[list[RepoStatus], int]:
    """Collect status for ``(label, path)`` pairs; returns rows and failed fetches.

    Rows come back sorted by label so the table is stable across runs.
    """
    items = sorted(repos)
    failed_fetches = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        if fetch:
            outcomes = run_bounded_pool(
                pool,
                items,
                lambda item: fetch_repo(item[1]),
                max_in_flight=jobs,
            )
            failed_fetches = sum(1 for ok in outcomes if not ok)
        rows = run_bounded_pool(
            pool,
            items,
            lambda item: collect_status(item[1], item[0]),
            max_in_flight=jobs,
        )
    rows.sort(key=lambda r: r.label)
    return rows, failed_fetches


def repo_paths_to_labels(repos: dict[str, str], roots: list[str]) -> list[tuple[str, str]]:
    """Turn discovery output (``common_dir -> worktree``) into ``(label, worktree)``.

    Two roots can produce the same root-relative label. Keying by label would
    drop one of them silently, so a colliding label falls back to the absolute
    path for every repo that shares it.
    """
    by_label: dict[str, list[str]] = {}
    for worktree in sorted(set(repos.values())):
        by_label.setdefault(repo_label(Path(worktree), roots), []).append(worktree)

    labelled: list[tuple[str, str]] = []
    for label, worktrees in by_label.items():
        if len(worktrees) == 1:
            labelled.append((label, worktrees[0]))
            continue
        labelled.extend((worktree, worktree) for worktree in worktrees)
    return sorted(labelled)
