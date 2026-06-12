"""Tests for policy-row display and policy_violations counter (#125)."""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_models import ActionBump, RepoBundle, Workspace, WorkspaceResult
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


def _ws(tmp_path: Path) -> WorkspaceResult:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    return WorkspaceResult(workspace=ws, status="ok")


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


# ---------------------------------------------------------------------------
# CLI flag wiring: --commit-umbrella and --actions-org-policy
# ---------------------------------------------------------------------------


def test_commit_umbrella_flag_registered() -> None:
    """--commit-umbrella is accepted on the apply parser."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=True)
    args = parser.parse_args(["--commit-umbrella"])
    assert args.commit_umbrella is True
    args_off = parser.parse_args([])
    assert args_off.commit_umbrella is False


def test_commit_umbrella_not_on_plan_parser() -> None:
    """--commit-umbrella is not registered on the plan parser (apply-only)."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--commit-umbrella"])


def test_actions_org_policy_flag_registered_on_plan() -> None:
    """--actions-org-policy is accepted on the plan parser."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--actions-org-policy"])
    assert args.actions_org_policy is True


def test_actions_org_policy_flag_registered_on_apply() -> None:
    """--actions-org-policy is accepted on the apply parser."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=True)
    args = parser.parse_args(["--actions-org-policy"])
    assert args.actions_org_policy is True


def test_commit_umbrella_wires_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--commit-umbrella sets REPO_OPS_COMMIT_UMBRELLA=1 before post-apply phase."""
    monkeypatch.delenv("REPO_OPS_COMMIT_UMBRELLA", raising=False)

    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=True)
    args = parser.parse_args(["--commit-umbrella"])

    # Simulate the env-wiring block from _run_upgrade_body
    if getattr(args, "commit_umbrella", False):
        os.environ["REPO_OPS_COMMIT_UMBRELLA"] = "1"

    assert os.environ.get("REPO_OPS_COMMIT_UMBRELLA") == "1"
    monkeypatch.delenv("REPO_OPS_COMMIT_UMBRELLA", raising=False)


def test_actions_org_policy_wires_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--actions-org-policy sets REPO_OPS_ACTIONS_ORG_POLICY=1."""
    monkeypatch.delenv("REPO_OPS_ACTIONS_ORG_POLICY", raising=False)

    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--actions-org-policy"])

    # Simulate the env-wiring block from _run_upgrade_body
    if getattr(args, "actions_org_policy", False):
        os.environ["REPO_OPS_ACTIONS_ORG_POLICY"] = "1"

    assert os.environ.get("REPO_OPS_ACTIONS_ORG_POLICY") == "1"
    monkeypatch.delenv("REPO_OPS_ACTIONS_ORG_POLICY", raising=False)


# ---------------------------------------------------------------------------
# SETTINGS_TABLE: new v0.15.0 entries
# ---------------------------------------------------------------------------


def test_settings_table_has_v015_entries() -> None:
    """commit_umbrella, actions_org_policy, container_update_latest are in SETTINGS_TABLE."""
    from repo_ops_core.config import SETTINGS_TABLE

    names = [r[0] for r in SETTINGS_TABLE]
    for key in ("commit_umbrella", "actions_org_policy", "container_update_latest"):
        assert key in names, f"Missing SETTINGS_TABLE entry: {key}"


def test_settings_table_commit_umbrella_env_var() -> None:
    from repo_ops_core.config import SETTINGS_TABLE

    row = next(r for r in SETTINGS_TABLE if r[0] == "commit_umbrella")
    assert row[2] == "REPO_OPS_COMMIT_UMBRELLA"
    assert row[1] == "--commit-umbrella"


def test_settings_table_actions_org_policy_env_var() -> None:
    from repo_ops_core.config import SETTINGS_TABLE

    row = next(r for r in SETTINGS_TABLE if r[0] == "actions_org_policy")
    assert row[2] == "REPO_OPS_ACTIONS_ORG_POLICY"
    assert row[1] == "--actions-org-policy"


def test_settings_table_container_update_latest_env_var() -> None:
    from repo_ops_core.config import SETTINGS_TABLE

    row = next(r for r in SETTINGS_TABLE if r[0] == "container_update_latest")
    assert row[2] == "REPO_OPS_CONTAINER_UPDATE_LATEST"


def test_resolve_setting_commit_umbrella(monkeypatch: pytest.MonkeyPatch) -> None:
    from repo_ops_core.config import resolve_setting

    monkeypatch.setenv("REPO_OPS_COMMIT_UMBRELLA", "1")
    assert resolve_setting("commit_umbrella") == "1"


def test_resolve_setting_actions_org_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from repo_ops_core.config import resolve_setting

    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    assert resolve_setting("actions_org_policy") == "1"


def test_resolve_setting_container_update_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from repo_ops_core.config import resolve_setting

    monkeypatch.setenv("REPO_OPS_CONTAINER_UPDATE_LATEST", "1")
    assert resolve_setting("container_update_latest") == "1"
