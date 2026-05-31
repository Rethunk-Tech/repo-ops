"""--json-out writes a single JSON artifact; stdout is the path only."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_upgrade.report import build_json_payload
from repo_ops_upgrade.run import main_plan


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def test_plan_json_out_writes_file_and_prints_path_only(
    tmp_path: Path, capsys
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    out_file = tmp_path / "plan.json"
    register_all()

    def _noop_workspace(wr, **_kwargs):
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_noop_workspace,
    ):
        rc = main_plan(
            [
                str(tmp_path),
                "--json",
                f"--json-out={out_file}",
                "--flat",
                "--skip-actions",
            ]
        )

    captured = capsys.readouterr()
    assert captured.out.strip() == str(out_file.resolve())
    assert captured.err == ""

    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written["mode"] == "plan"
    assert written["roots"] == [str(tmp_path)]
    assert "repos" in written
    assert rc in (0, 1)


def test_json_out_requires_json(tmp_path: Path) -> None:
    register_all()
    with patch("repo_ops_upgrade.run._run_upgrade_body", return_value=0):
        try:
            main_plan(
                [
                    str(tmp_path),
                    f"--json-out={tmp_path / 'out.json'}",
                    "--flat",
                ]
            )
        except SystemExit as exc:
            assert exc.code != 0
        else:
            raise AssertionError("expected SystemExit")


def test_write_json_report_matches_build_json_payload(tmp_path: Path) -> None:
    from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import write_json_report

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    wr = WorkspaceResult(workspace=ws, changes=[Change("pkg", "1.0", "2.0")])
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    bundles = {tmp_path: bundle}

    target = tmp_path / "nested" / "report.json"
    written = write_json_report(
        target,
        bundles,
        roots=["/tmp"],
        mode="plan",
    )
    assert written == target.resolve()
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    expected = build_json_payload(bundles, roots=["/tmp"], mode="plan")
    assert on_disk == expected
