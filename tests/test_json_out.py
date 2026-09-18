"""Every plan/apply writes its transcript and JSON report into a run dir."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_core.filesystem import new_run_dir
from repo_ops_upgrade.run import main_apply, main_plan


def _npm_repo(root: Path, init_git_repo) -> None:
    init_git_repo(root)
    (root / "package.json").write_text('{"name":"root","packageManager":"npm@10.0.0"}')
    (root / "package-lock.json").write_text("{}")


def test_plan_writes_log_and_report_and_prints_paths_last(
    tmp_path: Path, capsys, init_git_repo, run_dir: Path
) -> None:
    repo = tmp_path / "repo"
    _npm_repo(repo, init_git_repo)
    register_all()
    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace", side_effect=lambda wr, **_: wr
    ):
        rc = main_plan([str(repo)])

    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == f"repo-ops: log {run_dir / 'log'}  report {run_dir / 'report.json'}"
    report = json.loads((run_dir / "report.json").read_text())
    assert report["mode"] == "plan"
    assert report["exit_code"] == rc
    assert "exit_code:" in (run_dir / "log").read_text()


def test_apply_report_keeps_colocated_npm_and_container(
    tmp_path: Path, init_git_repo, run_dir: Path
) -> None:
    """Two WorkspaceResults sharing a path both survive result reconciliation."""
    _npm_repo(tmp_path, init_git_repo)
    (tmp_path / "Dockerfile").write_text("FROM alpine:3.19\n")
    register_all()

    def _stamp(wr, **_kwargs):
        wr.status = "ok"
        wr.error = f"processed:{wr.workspace.manager}"
        return wr

    with patch("repo_ops_upgrade.run.orchestration.process_workspace", side_effect=_stamp):
        rc = main_apply([str(tmp_path), "--no-commit"])

    assert rc == 0
    payload = json.loads((run_dir / "report.json").read_text())
    by_manager = {ws["manager"]: ws for repo in payload["repos"] for ws in repo["workspaces"]}
    assert by_manager["npm"]["error"] == "processed:npm"
    assert by_manager["container"]["error"] == "processed:container"


def test_run_dirs_keep_only_the_newest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr("repo_ops_core.filesystem._RUNS_KEPT", 2)
    made = [new_run_dir("plan") for _ in range(3)]
    kept = sorted((tmp_path / "repo-ops" / "runs").iterdir())
    assert kept == made[1:]
