"""Discovery diagnostics and the plan report.json contract."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_upgrade.discovery import DiscoveryDiagnostics, discover_all
from repo_ops_upgrade.run import main_plan


def test_discover_all_json_diagnostics_no_stderr(tmp_path: Path, capsys, init_git_repo) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    skip_dir = tmp_path / "skip-pkg"
    skip_dir.mkdir()
    (skip_dir / "package.json").write_text('{"name":"skip"}', encoding="utf-8")

    warn_dir = tmp_path / "warn-pkg"
    warn_dir.mkdir()
    (warn_dir / "package.json").write_text(
        '{"name":"warn","packageManager":"yarn@4.0.0"}',
        encoding="utf-8",
    )
    (warn_dir / "bun.lock").write_text("", encoding="utf-8")

    diag = DiscoveryDiagnostics()
    discover_all([str(tmp_path)], diagnostics=diag)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(diag.skipped) == 1
    assert diag.skipped[0]["ecosystem"] == "node"
    assert "no packageManager" in diag.skipped[0]["reason"]
    assert len(diag.warnings) == 1
    assert "packageManager=yarn" in diag.warnings[0]["message"]


def test_plan_report_records_skips_and_warnings(
    tmp_path: Path, capsys, init_git_repo, run_dir: Path
) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    skip_dir = tmp_path / "skip-pkg"
    skip_dir.mkdir()
    (skip_dir / "package.json").write_text('{"name":"skip"}', encoding="utf-8")

    warn_dir = tmp_path / "warn-pkg"
    warn_dir.mkdir()
    (warn_dir / "package.json").write_text(
        '{"name":"warn","packageManager":"yarn@4.0.0"}',
        encoding="utf-8",
    )
    (warn_dir / "bun.lock").write_text("", encoding="utf-8")

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
            ]
        )

    payload = json.loads((run_dir / "report.json").read_text())
    assert payload["mode"] == "plan"
    assert len(payload["skipped"]) >= 1
    assert len(payload["warnings"]) >= 1
    assert "generated_at" in payload
    assert rc in (0, 1)


def test_plan_exclude_filters_repositories_from_config(
    tmp_path: Path, monkeypatch, capsys, init_git_repo, run_dir: Path
) -> None:
    init_git_repo(tmp_path)
    repos = {}
    for name in ("keep", "drop"):
        repo = tmp_path / name
        repo.mkdir()
        init_git_repo(repo)
        (repo / "package.json").write_text(
            f'{{"name":"{name}","packageManager":"npm@10.0.0"}}',
            encoding="utf-8",
        )
        (repo / "package-lock.json").write_text("{}", encoding="utf-8")
        repos[name] = repo

    monkeypatch.chdir(tmp_path)
    register_all()
    seen: list[Path] = []

    def _record_workspace(wr, **_kwargs):
        seen.append(wr.workspace.path)
        return wr

    with (
        # The global config's exclude list applies to discovered repos.
        patch("repo_ops_upgrade.run.load_global_config", return_value={"exclude": ["drop"]}),
        patch(
            "repo_ops_upgrade.run.orchestration.process_workspace",
            side_effect=_record_workspace,
        ),
    ):
        rc = main_plan([str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads((run_dir / "report.json").read_text())
    assert rc in (0, 1)
    assert seen == [repos["keep"].resolve()]
    assert payload["summary"]["excluded"] == 1
    assert "excluded 1 repo(s) by exclude patterns" in captured.err
