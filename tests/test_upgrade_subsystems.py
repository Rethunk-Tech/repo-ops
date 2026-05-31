"""Mocked tests for upgrade actions, remediations, orchestration, and report."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.report import build_json_payload, print_human_report


def _sample_workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="test",
    )


def test_build_json_payload_shape(tmp_path: Path) -> None:
    ws = _sample_workspace(tmp_path)
    wr = WorkspaceResult(workspace=ws, changes=[Change("pkg", "1.0", "2.0")])
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    payload = build_json_payload(
        {tmp_path: bundle},
        roots=["/tmp"],
        mode="plan",
    )
    assert payload["mode"] == "plan"
    assert payload["roots"] == ["/tmp"]
    assert payload["warnings"] == []
    assert payload["skipped"] == []
    assert len(payload["repos"]) == 1
    assert payload["repos"][0]["label"] == "demo"
    assert payload["repos"][0]["actions_summary"]["workflows_scanned"] == 0


def test_print_human_report_header(capsys) -> None:
    print_human_report(
        {},
        dry_run=True,
        only_outdated=False,
        top_n=10,
        include_actions=False,
    )
    out = capsys.readouterr().out
    assert "repo-ops plan" in out


def test_scan_actions_empty_repo(tmp_path: Path) -> None:
    from repo_ops_upgrade.actions import scan_actions

    bumps, scanned = scan_actions(tmp_path, apply=False)
    assert bumps == []
    assert scanned == 0


def test_actions_summary_in_json_and_human_report(tmp_path: Path) -> None:
    from repo_ops_upgrade.report import build_json_payload, print_human_report

    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - uses: actions/checkout@v3\n",
        encoding="utf-8",
    )
    bundle = RepoBundle(
        git_repo=tmp_path,
        label="demo",
        actions_workflows_scanned=1,
    )
    payload = build_json_payload({tmp_path: bundle}, roots=["/tmp"], mode="plan")
    assert payload["repos"][0]["actions_summary"]["workflows_scanned"] == 1

    import io
    import sys

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=True,
        )
    out = buf.getvalue()
    assert "1 workflow(s) scanned" in out


def test_run_remediations_no_detected(tmp_path: Path) -> None:
    from repo_ops_upgrade_remediations_js import remediations

    ws = _sample_workspace(tmp_path)
    result = WorkspaceResult(workspace=ws)
    with patch.object(remediations, "REMEDIATIONS", []):
        remediations.run_remediations(result, apply=False, skip=set())
    assert result.remediations == []


def test_process_workspace_plan_mode(tmp_path: Path) -> None:
    from repo_ops_upgrade import orchestration

    register_all()
    ws = _sample_workspace(tmp_path)
    wr = WorkspaceResult(workspace=ws)
    checked = WorkspaceResult(
        workspace=ws,
        changes=[Change("dep", "1", "2")],
    )

    with (
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.check_workspace",
            return_value=checked,
        ),
        patch("repo_ops_upgrade.orchestration.remediations.run_remediations"),
    ):
        out = orchestration.process_workspace(
            wr,
            do_apply=False,
            do_verify=False,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
        )
    assert out.changes[0].name == "dep"
