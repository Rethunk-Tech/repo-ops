"""JSON stdout contract for repo-ops plan --json."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_upgrade.discovery import DiscoveryDiagnostics, discover_all
from repo_ops_upgrade.run import main_plan


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def test_discover_all_json_diagnostics_no_stderr(tmp_path: Path, capsys) -> None:
    _init_git_repo(tmp_path)
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
    discover_all([str(tmp_path)], flat=True, diagnostics=diag)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(diag.skipped) == 1
    assert diag.skipped[0]["ecosystem"] == "node"
    assert "no packageManager" in diag.skipped[0]["reason"]
    assert len(diag.warnings) == 1
    assert "packageManager=yarn" in diag.warnings[0]["message"]


def test_plan_json_stdout_parses_without_skip_warn_lines(
    tmp_path: Path, capsys
) -> None:
    _init_git_repo(tmp_path)
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
                "--json",
                "--flat",
                "--skip-actions",
            ]
        )

    captured = capsys.readouterr()
    for line in captured.out.splitlines():
        assert not line.startswith("SKIP ")
        assert not line.startswith("WARN ")
    assert "SKIP" not in captured.err
    assert not any(line.startswith("WARN ") for line in captured.err.splitlines())

    payload = json.loads(captured.out)
    assert payload["mode"] == "plan"
    assert len(payload["skipped"]) >= 1
    assert len(payload["warnings"]) >= 1
    assert rc in (0, 1)
