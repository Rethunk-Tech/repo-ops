"""Tests for policy-row display and policy_violations counter."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_models import ActionBump, RepoBundle
from repo_ops_upgrade.report import (
    build_run_summary,
    print_human_report,
    print_run_footer,
)


def _bundle(
    tmp_path: Path,
    *,
    label: str = "demo",
    actions: list[ActionBump] | None = None,
    workflows_scanned: int = 0,
) -> RepoBundle:
    return RepoBundle(
        git_repo=tmp_path,
        label=label,
        workspaces=[],
        actions=actions or [],
        actions_workflows_scanned=workflows_scanned,
    )


# ---------------------------------------------------------------------------
# build_run_summary: policy_violations count
# ---------------------------------------------------------------------------


def test_build_run_summary_counts_policy_violations(tmp_path: Path) -> None:
    """policy_violations accumulates across bundles."""
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            actions=[
                ActionBump("ci.yml", "actions/checkout", "v3", "v4", status="ok"),
                ActionBump(
                    "ci.yml",
                    "bad-corp/evil-action",
                    "v1",
                    "v1",
                    status="policy",
                    detail="action not in org allowlist",
                ),
                ActionBump(
                    "ci.yml",
                    "bad-corp/other",
                    "v2",
                    "v2",
                    status="policy",
                    detail="action not in org allowlist",
                ),
            ],
            workflows_scanned=1,
        ),
    }
    summary = build_run_summary(bundles)
    assert summary["actions_policy_violations"] == 2
    # policy rows must NOT inflate bumps or failures
    assert summary["actions_bumps"] == 1
    assert summary["actions_failures"] == 0


def test_build_run_summary_policy_violations_zero_when_none(tmp_path: Path) -> None:
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            actions=[ActionBump("ci.yml", "actions/checkout", "v3", "v4", status="ok")],
        ),
    }
    summary = build_run_summary(bundles)
    assert summary["actions_policy_violations"] == 0


# ---------------------------------------------------------------------------
# print_human_report: policy rows rendered
# ---------------------------------------------------------------------------


def test_print_human_report_renders_policy_rows(tmp_path: Path) -> None:
    """Policy-status ActionBump rows appear in the Actions block."""
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            actions=[
                ActionBump(
                    "ci.yml",
                    "bad-corp/evil-action",
                    "v1",
                    "v1",
                    status="policy",
                    detail="action not in org allowlist",
                ),
            ],
            workflows_scanned=1,
        ),
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            bundles,
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=True,
        )
    out = buf.getvalue()
    assert "bad-corp/evil-action" in out
    assert "[policy]" in out
    assert "action not in org allowlist" in out


@pytest.mark.parametrize("status", ["policy", "policy-blocked", "warn"])
def test_print_human_report_renders_policy_status_marker(tmp_path: Path, status: str) -> None:
    """All policy statuses render as policy rows, not action bumps."""
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            actions=[
                ActionBump(
                    "ci.yml",
                    "bad-corp/evil-action",
                    "v1",
                    "v2",
                    status=status,
                    detail="action not in org allowlist",
                ),
            ],
            workflows_scanned=1,
        ),
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            bundles,
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=True,
        )
    out = buf.getvalue()
    assert f"[{status}]" in out
    assert "→" not in out


def test_print_human_report_policy_violation_count_line(tmp_path: Path) -> None:
    """When policy rows exist, a per-bundle count line appears."""
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            actions=[
                ActionBump(
                    "ci.yml",
                    "bad-corp/evil-action",
                    "v1",
                    "v1",
                    status="policy",
                    detail="action not in org allowlist",
                ),
            ],
            workflows_scanned=1,
        ),
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            bundles,
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=True,
        )
    out = buf.getvalue()
    assert "Actions policy violations: 1" in out


def test_print_human_report_no_policy_count_when_zero(tmp_path: Path) -> None:
    """No extra policy line when there are no violations."""
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            actions=[
                ActionBump("ci.yml", "actions/checkout", "v3", "v4", status="ok"),
            ],
            workflows_scanned=1,
        ),
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            bundles,
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=True,
        )
    out = buf.getvalue()
    assert "policy violations" not in out.lower()


# ---------------------------------------------------------------------------
# print_run_footer: actions policy violations row
# ---------------------------------------------------------------------------


def test_print_run_footer_includes_policy_violations_row() -> None:
    """Footer summary table contains actions policy violations count."""
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 2,
        "actions_failures": 0,
        "actions_policy_violations": 3,
        "discovery_skipped": 0,
        "failure_count": 0,
        "packages_upgraded": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "actions policy violations" in out
    assert "3" in out


def test_print_run_footer_policy_violations_zero() -> None:
    """Footer handles missing or zero policy_violations without error."""
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "failure_count": 0,
        "packages_upgraded": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    # Should still render the row with 0
    assert "actions policy violations" in out
    assert "0" in out
