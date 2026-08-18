"""--json-out writes a single JSON artifact; stdout is the path only."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_upgrade.report import build_json_payload
from repo_ops_upgrade.run import main_apply, main_plan


def test_plan_json_out_writes_file_and_prints_path_only(
    tmp_path: Path, capsys, init_git_repo
) -> None:
    init_git_repo(tmp_path)
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


def test_apply_json_out_keeps_colocated_npm_and_container(tmp_path: Path, init_git_repo) -> None:
    """Co-located ecosystems must both survive result reconciliation.

    A directory with package.json + Dockerfile yields two WorkspaceResults that
    share a path. Path-only keys in the post-pool map used to overwrite the
    earlier (npm) result with the later (container) one, so --json-out dropped
    the non-container workspace.
    """
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM alpine:3.19\n", encoding="utf-8")

    out_file = tmp_path / "apply.json"
    register_all()
    seen_managers: list[str] = []

    def _stamp(wr, **_kwargs):
        seen_managers.append(wr.workspace.manager)
        wr.status = "ok"
        wr.error = f"processed:{wr.workspace.manager}"
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_stamp,
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

    assert rc == 0
    assert "npm" in seen_managers
    assert "container" in seen_managers

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    workspaces = [ws for repo in payload["repos"] for ws in repo["workspaces"]]
    managers = {ws["manager"] for ws in workspaces}
    assert managers == {"npm", "container"}
    by_manager = {ws["manager"]: ws for ws in workspaces}
    assert by_manager["npm"]["error"] == "processed:npm"
    assert by_manager["container"]["error"] == "processed:container"


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

    # generated_at is stamped at call time; normalise before comparing structure.
    assert "generated_at" in on_disk
    on_disk_normalised = {k: v for k, v in on_disk.items() if k != "generated_at"}

    expected = build_json_payload(bundles, roots=["/tmp"], mode="plan")
    expected_normalised = {k: v for k, v in expected.items() if k != "generated_at"}
    assert on_disk_normalised == expected_normalised


def test_write_json_report_includes_exit_code(tmp_path: Path) -> None:
    from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import write_json_report

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    wr = WorkspaceResult(workspace=ws)
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    bundles = {tmp_path: bundle}

    target = tmp_path / "report.json"
    write_json_report(target, bundles, roots=["/tmp"], mode="plan", exit_code=0)
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["exit_code"] == 0
    assert "generated_at" in on_disk


def test_write_json_report_omits_exit_code_when_none(tmp_path: Path) -> None:
    from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import write_json_report

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    wr = WorkspaceResult(workspace=ws)
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    bundles = {tmp_path: bundle}

    target = tmp_path / "report.json"
    write_json_report(target, bundles, roots=["/tmp"], mode="plan")
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert "exit_code" not in on_disk


# ---------- inventory / pushed json-out contracts ----------


def test_inventory_json_out_writes_file_and_prints_path_only(tmp_path: Path, capsys) -> None:
    from repo_ops_inventory.scan import main as inventory_main

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    out_file = tmp_path / "inventory.json"
    rc = inventory_main([str(tmp_path), "--flat", "--json", f"--json-out={out_file}"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(out_file.resolve())
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "summary" in payload
    assert "rows" in payload


def test_pushed_json_out_writes_file_and_prints_path_only(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from repo_ops_pushed import audit
    from repo_ops_pushed.audit import main as pushed_main

    monkeypatch.setattr(audit, "collect_results", lambda *a, **kw: ({}, []))
    out_file = tmp_path / "pushed.json"
    rc = pushed_main([str(tmp_path), "--json", f"--json-out={out_file}"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(out_file.resolve())
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "summary" in payload
