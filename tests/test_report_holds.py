"""Hold-declined bumps must be visible in the behind-latest report bucket.

Covers the row annotation, the per-workspace section header, and the
``packages_held`` summary counter added to make a hold failure visible
instead of indistinguishable from an ordinary behind-latest package.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.report import build_run_summary, print_human_report


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )


def _bundle(tmp_path: Path, wr: WorkspaceResult) -> RepoBundle:
    return RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])


def _write_hold_config(tmp_path: Path, *, until: str | None = None) -> None:
    body = '[[hold]]\npackage = "typescript"\nmax_version = "6"\n'
    if until:
        body += f'until = "{until}"\n'
    (tmp_path / ".repo-ops.toml").write_text(body, encoding="utf-8")


def _render_and_summarize(tmp_path: Path, bundle: RepoBundle) -> tuple[str, dict]:
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=False,
        )
    summary = build_run_summary({tmp_path: bundle})
    return buf.getvalue(), summary


def test_held_behind_latest_row_header_and_summary(tmp_path: Path) -> None:
    """A repo-scoped hold that declines a bump is named in the row, the
    section header gets a held count, and the summary counts it."""
    _write_hold_config(tmp_path, until="2026-11-01")
    wr = WorkspaceResult(
        workspace=_ws(tmp_path),
        behind_latest=[Change("typescript", "5.9.0", "7.0.0")],
    )
    out, summary = _render_and_summarize(tmp_path, _bundle(tmp_path, wr))

    assert "behind-latest: 1 package(s) not actioned, 1 held (" in out
    assert "typescript" in out
    assert "→ 7.0.0 (latest; held <= 6, until 2026-11-01)" in out
    assert summary["packages_held"] == 1
    assert summary["packages_behind_latest"] == 1


def test_no_holds_configured_output_and_summary_unchanged(tmp_path: Path) -> None:
    """No `.repo-ops.toml`/pyproject.toml anywhere (the common case) — row,
    header, and summary must render exactly as before hold visibility."""
    wr = WorkspaceResult(
        workspace=_ws(tmp_path),
        behind_latest=[Change("typescript", "5.9.0", "7.0.0")],
    )
    out, summary = _render_and_summarize(tmp_path, _bundle(tmp_path, wr))

    assert "behind-latest: 1 package(s) not actioned (" in out
    assert ", 1 held" not in out
    assert "→ 7.0.0 (latest)" in out
    assert summary["packages_held"] == 0
    assert summary["packages_behind_latest"] == 1
