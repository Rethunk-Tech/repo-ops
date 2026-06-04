"""Regression tests for behind-latest disposition (#22, #42).

Verifies that workspaces with newer-than-installed packages when latest-mode
is off, and Actions with a newer major available, are reported as a distinct
``behind_latest`` disposition (NOT ok / NOT fail), do not change exit code,
and appear in the footer hint.

Also covers #42: node/bun tuple-checker with empty behind_latest still triggers
``overrides_audit`` (the empty list must NOT suppress the call).
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import ActionBump, Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import registry
from repo_ops_upgrade.report import (
    build_run_summary,
    print_run_footer,
)
from repo_ops_upgrade.run import main_plan


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="demo",
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


# ---------------------------------------------------------------------------
# WorkspaceResult.behind_latest — field exists and JSON is additive
# ---------------------------------------------------------------------------


def test_workspace_result_behind_latest_field(tmp_path: Path) -> None:
    """WorkspaceResult has a behind_latest field, default empty."""
    ws = _ws(tmp_path)
    wr = WorkspaceResult(workspace=ws)
    assert wr.behind_latest == []
    assert wr.status == "ok"


def test_workspace_result_to_json_includes_behind_latest(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    wr = WorkspaceResult(
        workspace=ws,
        behind_latest=[Change("@hono/node-server", "1.19.14", "2.0.4")],
    )
    j = wr.to_json()
    assert "behind_latest" in j
    assert j["behind_latest"] == [
        {"name": "@hono/node-server", "from": "1.19.14", "to": "2.0.4"}
    ]
    # Existing fields must remain unchanged.
    assert "changes" in j
    assert "status" in j


# ---------------------------------------------------------------------------
# build_run_summary — packages_behind_latest counter
# ---------------------------------------------------------------------------


def test_build_run_summary_counts_behind_latest(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    wr = WorkspaceResult(
        workspace=ws,
        status="ok",
        changes=[],
        behind_latest=[
            Change("pkg-a", "1.0.0", "2.0.0"),
            Change("pkg-b", "3.0.0", "4.0.0"),
        ],
    )
    bundles = {tmp_path: _bundle(tmp_path, workspaces=[wr])}
    summary = build_run_summary(bundles)
    assert summary["packages_behind_latest"] == 2
    # Status is still ok — no regression in existing counters.
    assert summary["workspaces_ok"] == 1
    assert summary["workspaces_fail"] == 0


def test_behind_latest_does_not_affect_failure_count(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    wr = WorkspaceResult(
        workspace=ws,
        status="ok",
        behind_latest=[Change("lodash", "4.17.21", "5.0.0")],
    )
    bundles = {tmp_path: _bundle(tmp_path, workspaces=[wr])}
    summary = build_run_summary(bundles)
    assert summary["failure_count"] == 0
    assert summary["packages_behind_latest"] == 1


# ---------------------------------------------------------------------------
# Actions behind_latest counter — "newer major available; not auto-applied"
# ---------------------------------------------------------------------------


def test_actions_summary_counts_newer_major_as_behind_latest(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump(
                workflow="ci.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
            ActionBump(
                workflow="ci.yml",
                action="actions/setup-node",
                pin="v3",
                latest="v4",
                status="ok",
                detail="normalize to floating major v3",
            ),
        ],
        workflows_scanned=1,
    )
    s = bundle.actions_summary()
    assert s["behind_latest"] == 1
    assert s["bumps"] == 1  # normalize bump is still actionable


def test_actions_summary_dedupes_behind_latest_across_workflows(
    tmp_path: Path,
) -> None:
    """Same action@pin with newer major across N workflows → behind_latest == 1 (#74)."""
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump(
                workflow="ci.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
            ActionBump(
                workflow="release.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
            ActionBump(
                workflow="deploy.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
        ],
        workflows_scanned=3,
    )
    s = bundle.actions_summary()
    assert s["behind_latest"] == 1
    assert s["bumps"] == 0


def test_build_run_summary_includes_actions_behind_latest(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump(
                "ci.yml",
                "actions/checkout",
                "v4",
                "v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
        ],
        workflows_scanned=1,
    )
    summary = build_run_summary({tmp_path: bundle})
    assert summary["actions_behind_latest"] == 1
    assert summary["failure_count"] == 0


# ---------------------------------------------------------------------------
# Footer hint — appears when behind_latest > 0
# ---------------------------------------------------------------------------


def test_footer_hint_appears_for_packages_behind_latest(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 3,
        "actions_behind_latest": 0,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "behind-latest" in out
    assert "3 package(s)" in out
    assert "REPO_OPS_BUN_UPDATE_LATEST=1" in out


def test_footer_hint_appears_for_actions_behind_latest(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 0,
        "actions_behind_latest": 2,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "behind-latest" in out
    assert "2 action(s)" in out
    assert "cross-major" in out


def test_footer_hint_absent_when_none_behind_latest(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 0,
        "actions_behind_latest": 0,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "behind-latest" not in out


# ---------------------------------------------------------------------------
# Exit code invariant — behind_latest must NOT change exit code to 1
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _bun_workspace(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


def test_behind_latest_workspace_does_not_change_plan_exit_code(
    tmp_path: Path,
) -> None:
    """A workspace with only behind_latest entries must yield exit_code == 0."""
    _bun_workspace(tmp_path)
    register_all()

    behind = [Change("@hono/node-server", "1.19.14", "2.0.4")]

    def _mock_check(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        # status=ok, no actionable changes, but behind_latest populated.
        wr.status = "ok"
        wr.changes = []
        wr.behind_latest = behind
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_mock_check,
    ):
        rc = main_plan(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--quiet",
            ]
        )
    assert rc == 0


# ---------------------------------------------------------------------------
# #42 — node tuple-checkers always call overrides_audit even when behind_latest
# is empty (the common case that triggered the regression)
# ---------------------------------------------------------------------------


def test_check_workspace_calls_overrides_audit_when_behind_latest_empty(
    tmp_path: Path,
) -> None:
    """Regression #42: bun checker returning ([], []) must still invoke overrides_audit."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="demo",
    )
    finding = {"pkg": "lodash", "pinned": "4.17.21", "detail": "stale override"}

    def fake_bun_checker(_ws: Workspace):
        # Simulate a clean run: no actionable changes, no behind_latest entries.
        return [], []

    with (
        patch.dict(registry.CHECKERS, {"bun": fake_bun_checker}, clear=False),
        patch(
            "repo_ops_upgrade.registry.overrides_audit",
            return_value=[finding],
        ) as mock_audit,
    ):
        result = registry.check_workspace(ws)

    mock_audit.assert_called_once_with(tmp_path, "bun")
    assert result.overrides_audit == [finding]
    assert result.behind_latest == []
    assert result.changes == []
