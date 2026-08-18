"""Human and JSON rendering for the inventory status mode."""

from __future__ import annotations

from typing import Any

from repo_ops_core.table import column_width, format_cell, join_row
from repo_ops_status.status import RepoStatus

_HEADERS = ("repo", "branch", "dirty", "ahead", "behind")

#: Shown for ahead/behind when a repo has no ref to compare against, so an
#: unmeasurable repo never reads as a measured zero.
UNKNOWN = "-"


def _counts(row: RepoStatus) -> tuple[str, str]:
    if row.error is not None:
        return UNKNOWN, UNKNOWN
    if not row.comparable:
        return UNKNOWN, UNKNOWN
    return str(row.ahead), str(row.behind)


def summarize(rows: list[RepoStatus]) -> dict[str, int]:
    """Roll up the fleet-level counters printed under the table."""
    return {
        "repos": len(rows),
        "ahead_commits": sum(r.ahead or 0 for r in rows if r.comparable),
        "behind_commits": sum(r.behind or 0 for r in rows if r.comparable),
        "repos_ahead": sum(1 for r in rows if r.comparable and r.ahead),
        "repos_behind": sum(1 for r in rows if r.comparable and r.behind),
        "repos_dirty": sum(1 for r in rows if r.dirty),
        "dirty_files": sum(r.dirty for r in rows),
        "repos_no_upstream": sum(1 for r in rows if not r.comparable and r.error is None),
        "repos_errored": sum(1 for r in rows if r.error is not None),
    }


def print_status_table(rows: list[RepoStatus], top: int | None = None) -> None:
    """Emit repo | branch | dirty | ahead | behind, then a fleet roll-up."""
    summary = summarize(rows)
    if not rows:
        print("No git repositories found under the scanned roots.")
        return

    shown = rows[:top] if top is not None else rows
    cells = [(r.label, r.branch, str(r.dirty), *_counts(r)) for r in shown]
    widths = [
        column_width([c[i] for c in cells], _HEADERS[i], cap=80 if i == 0 else None)
        for i in range(len(_HEADERS))
    ]
    aligns: list[Any] = ["left", "left", "right", "right", "right"]

    header = join_row(
        [format_cell(_HEADERS[i], widths[i], align=aligns[i]) for i in range(len(_HEADERS))]
    )
    print(header)
    print("-" * len(header))
    for cell in cells:
        print(
            join_row([format_cell(cell[i], widths[i], align=aligns[i]) for i in range(len(cell))])
        )
    if top is not None and len(rows) > top:
        print(f"... and {len(rows) - top} more")

    print()
    print(
        f"{summary['repos']} repo(s): "
        f"{summary['repos_ahead']} ahead ({summary['ahead_commits']} commits), "
        f"{summary['repos_behind']} behind ({summary['behind_commits']} commits), "
        f"{summary['repos_dirty']} dirty ({summary['dirty_files']} files)"
    )
    if summary["repos_no_upstream"]:
        print(
            f"{summary['repos_no_upstream']} repo(s) have no upstream to compare against ({UNKNOWN})"
        )
    if summary["repos_errored"]:
        print(f"{summary['repos_errored']} repo(s) could not be read; see the JSON mode for detail")


def status_payload(rows: list[RepoStatus], roots: list[str], *, fetched: bool) -> dict[str, Any]:
    """Build the ``--json`` document for status mode."""
    return {
        "schema_version": 1,
        "mode": "status",
        "roots": roots,
        # False means counts are only as fresh as each repo's last fetch.
        "fetched": fetched,
        "summary": summarize(rows),
        "rows": [
            {
                "repo": r.label,
                "path": r.path,
                "branch": r.branch,
                "upstream": r.upstream,
                "dirty": r.dirty,
                "ahead": r.ahead,
                "behind": r.behind,
                "empty": r.empty,
                "error": r.error,
            }
            for r in rows
        ],
    }
