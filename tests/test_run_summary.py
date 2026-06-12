"""End-of-run roll-up summary (#14)."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import ActionBump, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.discovery import DiscoveryDiagnostics
from repo_ops_upgrade.report import (
    build_json_payload,
    build_run_summary,
    collect_run_failures,
    print_run_footer,
)


def _bundle(
    tmp_path: Path,
    *,
    label: str = "demo",
    workspaces: list[WorkspaceResult] | None = None,
    actions: list[ActionBump] | None = None,
    workflows_scanned: int = 0,
) -> RepoBundle:
    return RepoBundle(
        git_repo=tmp_path,
        label=label,
        workspaces=workspaces or [],
        actions=actions or [],
        actions_workflows_scanned=workflows_scanned,
    )


def test_build_run_summary_counts(tmp_path: Path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            workspaces=[
                WorkspaceResult(workspace=ws, status="ok"),
                WorkspaceResult(
                    workspace=ws,
                    status="skip",
                    error="dirty git worktree (use --allow-dirty)",
                ),
                WorkspaceResult(
                    workspace=ws,
                    status="fail",
                    verify_error="build failed",
                ),
            ],
            actions=[
                ActionBump("ci.yml", "actions/checkout", "v3", "v4", status="ok"),
                ActionBump("ci.yml", "actions/cache", "v3", "v4", status="fail"),
            ],
            workflows_scanned=1,
        ),
    }
    diagnostics = DiscoveryDiagnostics()
    diagnostics.skip_node(tmp_path / "orphan", "no packageManager")

    summary = build_run_summary(bundles, diagnostics=diagnostics)

    assert summary["workspaces_ok"] == 1
    assert summary["workspaces_fail"] == 1
    assert summary["workspaces_skip"] == 1
    assert summary["dirty_skips"] == 1
    assert summary["verify_failures"] == 1
    assert summary["actions_bumps"] == 1
    assert summary["actions_failures"] == 1
    assert summary["discovery_skipped"] == 1
    assert summary["failure_count"] == 2


def test_collect_run_failures_sorted(tmp_path: Path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="z",
    )
    bundles = {
        tmp_path: _bundle(
            tmp_path,
            label="z-repo",
            workspaces=[
                WorkspaceResult(workspace=ws, status="fail", error="z fail"),
            ],
        ),
        tmp_path.parent: _bundle(
            tmp_path.parent,
            label="a-repo",
            workspaces=[
                WorkspaceResult(workspace=ws, status="fail", verify_error="a verify"),
            ],
        ),
    }
    failures = collect_run_failures(bundles)
    assert failures == [
        ("a-repo", "a verify"),
        ("z-repo", "z fail"),
    ]


def test_build_json_payload_includes_summary(tmp_path: Path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    bundle = _bundle(tmp_path, workspaces=[WorkspaceResult(workspace=ws)])
    payload = build_json_payload({tmp_path: bundle}, roots=["/tmp"], mode="plan")
    assert payload["summary"]["workspaces_ok"] == 1
    assert payload["summary"]["failure_count"] == 0


def test_build_json_payload_generated_at_and_exit_code(tmp_path: Path) -> None:
    """generated_at is always present; exit_code only when provided."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    bundle = _bundle(tmp_path, workspaces=[WorkspaceResult(workspace=ws)])
    bundles = {tmp_path: bundle}

    payload_no_code = build_json_payload(bundles, roots=["/tmp"], mode="plan")
    assert "generated_at" in payload_no_code
    # UTC ISO-8601 ending with Z
    assert payload_no_code["generated_at"].endswith("Z")
    assert "exit_code" not in payload_no_code

    payload_with_code = build_json_payload(
        bundles, roots=["/tmp"], mode="apply", exit_code=1
    )
    assert payload_with_code["exit_code"] == 1
    assert "generated_at" in payload_with_code


def test_print_run_footer_includes_actions_failures_row(tmp_path: Path) -> None:
    """#48 — actions_failures must appear in the footer summary table."""
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 3,
        "actions_failures": 2,
        "discovery_skipped": 0,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "actions failures" in out
    assert "2" in out


def test_print_run_footer_caps_failure_list(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 0,
        "workspaces_fail": 5,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 5,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "failure_count": 5,
    }
    failures = [(f"repo-{i}", f"reason {i}") for i in range(5)]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(
            summary=summary,
            failures=failures,
            exit_code=1,
            max_failures=2,
        )
    out = buf.getvalue()
    assert "repo-0: reason 0" in out
    assert "repo-1: reason 1" in out
    assert "repo-2:" not in out
    assert "... and 3 more" in out
    assert "failures: 5" in out
    assert "exit_code: 1" in out


def test_actions_summary_dedupes_bumps_by_action_pin(tmp_path: Path) -> None:
    # Same (action, pin) across two workflow files yields a single deduped
    # report row, so the bump counter must also count it once (issue #65).
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump("ci.yml", "actions/checkout", "v3", "v4", status="ok"),
            ActionBump("release.yml", "actions/checkout", "v3", "v4", status="ok"),
        ],
        workflows_scanned=2,
    )

    assert bundle.actions_summary()["bumps"] == 1
