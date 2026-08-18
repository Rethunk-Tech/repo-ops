"""--on-verify-fail=revert|keep after verify failure."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade import orchestration
from repo_ops_upgrade.post_apply import FAILED_CLEAN, FAILED_WITH_CHANGES
from repo_ops_upgrade.run import main_apply


def _npm_workspace(tmp_path: Path, init_git_repo) -> Workspace:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion":1,"packages":{}}', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )


def test_verify_fail_revert_restores_lockfile(tmp_path: Path, init_git_repo) -> None:
    ws = _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    original = lock.read_bytes()
    wr = WorkspaceResult(workspace=ws)
    register_all()

    def _mutate_apply(workspace: Workspace) -> None:
        lock.write_text('{"lockfileVersion":2,"mutated":true}', encoding="utf-8")

    with (
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace",
            side_effect=_mutate_apply,
        ),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            return_value="simulated verify failure",
        ),
        patch("repo_ops_upgrade.orchestration.remediations.run_remediations"),
    ):
        out = orchestration.process_workspace(
            wr,
            do_apply=True,
            do_verify=True,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
            on_verify_fail="revert",
        )

    assert out.status == "fail"
    assert out.verify_error == "simulated verify failure"
    assert out.verify_fail_disposition == FAILED_CLEAN
    assert lock.read_bytes() == original


def test_verify_fail_keep_leaves_changes(tmp_path: Path, init_git_repo) -> None:
    ws = _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    lock = tmp_path / "package-lock.json"
    wr = WorkspaceResult(workspace=ws)
    register_all()

    def _mutate_apply(workspace: Workspace) -> None:
        lock.write_text('{"lockfileVersion":2,"mutated":true}', encoding="utf-8")

    with (
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace",
            side_effect=_mutate_apply,
        ),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            return_value="simulated verify failure",
        ),
        patch("repo_ops_upgrade.orchestration.remediations.run_remediations"),
    ):
        out = orchestration.process_workspace(
            wr,
            do_apply=True,
            do_verify=True,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
            on_verify_fail="keep",
        )

    assert out.verify_fail_disposition == FAILED_WITH_CHANGES
    assert b"mutated" in lock.read_bytes()


def test_apply_json_includes_verify_fail_disposition(tmp_path: Path, capsys, init_git_repo) -> None:
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()
    out_file = tmp_path / "apply.json"

    def _fail_verify(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        wr.status = "fail"
        wr.verify_error = "simulated"
        wr.verify_fail_disposition = FAILED_WITH_CHANGES
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_fail_verify,
    ):
        rc = main_apply(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--json",
                f"--json-out={out_file}",
            ]
        )

    assert rc == 1
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    ws_json = payload["repos"][0]["workspaces"][0]
    assert ws_json["verify_fail_disposition"] == FAILED_WITH_CHANGES


def test_apply_parser_exposes_on_verify_fail() -> None:
    from repo_ops_core.cli import build_upgrade_parser

    apply_parser = build_upgrade_parser(apply=True)
    dests = {a.dest for a in apply_parser._actions}
    assert "on_verify_fail" in dests
    plan_parser = build_upgrade_parser(apply=False)
    plan_dests = {a.dest for a in plan_parser._actions}
    assert "on_verify_fail" not in plan_dests
