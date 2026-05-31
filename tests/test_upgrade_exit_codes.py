"""Exit codes, --log-file, and --strict-exit for plan/apply."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade.run import main_apply, main_plan


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _npm_workspace(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")


def _fail_verify(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
    wr.status = "fail"
    wr.verify_error = "simulated verify failure"
    return wr


def test_apply_verify_failure_exits_nonzero(tmp_path: Path) -> None:
    _npm_workspace(tmp_path)
    register_all()
    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_fail_verify,
    ):
        rc = main_apply(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
            ]
        )
    assert rc == 1


def test_apply_subprocess_exit_without_pipefail(tmp_path: Path) -> None:
    """Child process exit code is non-zero without shell pipefail."""
    _npm_workspace(tmp_path)
    register_all()
    script = f"""
import sys
from unittest.mock import patch
from repo_ops._register import register_all
from repo_ops_models import WorkspaceResult
from repo_ops_upgrade.run import main_apply

def _fail(wr, **_kwargs):
    wr.status = "fail"
    wr.verify_error = "simulated verify failure"
    return wr

register_all()
with patch(
    "repo_ops_upgrade.run.orchestration.process_workspace",
    side_effect=_fail,
):
    sys.exit(main_apply([
        {str(tmp_path)!r},
        "--flat",
        "--skip-actions",
        "--quiet",
    ]))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1


def test_apply_log_file_captures_transcript(tmp_path: Path) -> None:
    _npm_workspace(tmp_path)
    log_path = tmp_path / "apply.log"
    register_all()
    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_fail_verify,
    ):
        rc = main_apply(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--quiet",
                "--log-file",
                str(log_path),
            ]
        )
    assert rc == 1
    transcript = log_path.read_text(encoding="utf-8")
    assert "exit_code: 1" in transcript
    assert "failures: 1" in transcript


def test_plan_log_file_includes_footer(tmp_path: Path, capsys) -> None:
    _npm_workspace(tmp_path)
    log_path = tmp_path / "plan.log"
    register_all()

    def _noop(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_noop,
    ):
        rc = main_plan(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--quiet",
                "--log-file",
                str(log_path),
            ]
        )
    assert rc == 0
    assert "exit_code: 0" in log_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "exit_code: 0" in captured.out


def _dirty_skip(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
    wr.status = "skip"
    wr.error = "dirty git worktree (use --allow-dirty)"
    return wr


def test_strict_exit_counts_dirty_skip(tmp_path: Path) -> None:
    _npm_workspace(tmp_path)
    register_all()

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_dirty_skip,
    ):
        rc_normal = main_apply(
            [str(tmp_path), "--flat", "--skip-actions", "--quiet"]
        )
        rc_strict = main_apply(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--quiet",
                "--strict-exit",
            ]
        )
    assert rc_normal == 0
    assert rc_strict == 1


def test_upgrade_parser_exposes_log_file_and_strict_exit() -> None:
    from repo_ops_core.cli import build_upgrade_parser

    for apply in (False, True):
        parser = build_upgrade_parser(apply=apply)
        dests = {a.dest for a in parser._actions}
        assert "log_file" in dests
        assert "strict_exit" in dests
