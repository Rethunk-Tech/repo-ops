"""Regression tests for behind-latest disposition.

Verifies that packages the sweep could not move, and Actions with a newer
major available, are reported as a distinct
``behind_latest`` disposition (NOT ok / NOT fail), do not change exit code,
and appear in the footer hint.

Also covers: node/bun tuple-checker with empty behind_latest still triggers
``overrides_audit`` (the empty list must NOT suppress the call).
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import ActionBump, Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import registry
from repo_ops_upgrade.report import (
    build_run_summary,
    print_human_report,
    print_run_footer,
)
from repo_ops_upgrade.run import main_plan


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


def test_workspace_result_behind_latest_field(tmp_path: Path, make_workspace) -> None:
    """WorkspaceResult has a behind_latest field, default empty."""
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    wr = WorkspaceResult(workspace=ws)
    assert wr.behind_latest == []
    assert wr.status == "ok"


def test_workspace_result_to_json_includes_behind_latest(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    wr = WorkspaceResult(
        workspace=ws,
        behind_latest=[Change("@hono/node-server", "1.19.14", "2.0.4")],
    )
    j = wr.to_json()
    assert "behind_latest" in j
    assert j["behind_latest"] == [{"name": "@hono/node-server", "from": "1.19.14", "to": "2.0.4"}]
    # Existing fields must remain unchanged.
    assert "changes" in j
    assert "status" in j


# ---------------------------------------------------------------------------
# build_run_summary — packages_behind_latest counter
# ---------------------------------------------------------------------------


def test_build_run_summary_counts_behind_latest(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
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


def test_behind_latest_does_not_affect_failure_count(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
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
    """Same action@pin with newer major across N workflows → behind_latest == 1."""
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


def test_yarn_behind_latest_note_when_targeted_bump_already_requested(
    tmp_path: Path, monkeypatch
) -> None:
    """Once --yarn-targeted-bump was requested, the yarn behind-latest note

    must report the fallback was tried and could not move the leftovers, instead
    of re-suggesting the flag the operator already passed.
    """
    yarn_ws = Workspace(
        path=tmp_path / "y",
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="demo",
    )
    bundle = _bundle(
        tmp_path,
        workspaces=[
            WorkspaceResult(
                workspace=yarn_ws,
                behind_latest=[Change("@playwright/test", "1.60.0", "1.61.0")],
            ),
        ],
    )
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=True,
        )
    out = buf.getvalue()
    assert "the targeted bump was attempted but could not move them" in out
    # Must NOT tell the operator to pass a flag they already passed.
    assert "pass --yarn-targeted-bump to attempt" not in out


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


def _bun_workspace(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


def test_behind_latest_workspace_does_not_change_plan_exit_code(
    tmp_path: Path,
    init_git_repo,
) -> None:
    """A workspace with only behind_latest entries must yield exit_code == 0."""
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
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
            ]
        )
    assert rc == 0


# ---------------------------------------------------------------------------
# node tuple-checkers always call overrides_audit even when behind_latest
# is empty (the common case that triggered the regression)
# ---------------------------------------------------------------------------


def test_check_workspace_calls_overrides_audit_when_behind_latest_empty(
    tmp_path: Path,
) -> None:
    """Bun checker returning ([], []) must still invoke overrides_audit."""
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
