"""Exit codes, --log-file, and --strict-exit for plan/apply."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import WorkspaceResult
from repo_ops_upgrade.run import main_apply, main_plan


def _npm_workspace(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")


def _fail_verify(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
    wr.status = "fail"
    wr.verify_error = "simulated verify failure"
    return wr


def test_apply_verify_failure_exits_nonzero(tmp_path: Path, init_git_repo) -> None:
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
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


def test_apply_subprocess_exit_without_pipefail(tmp_path: Path, init_git_repo) -> None:
    """Child process exit code is non-zero without shell pipefail."""
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
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


def test_apply_worker_exception_reports_fail_not_clean(tmp_path: Path, init_git_repo) -> None:
    """A worker that raises must surface as a FAILED workspace, not a silent
    clean exit 0 (#62). Exit code alone is insufficient: the defensive
    fallthrough would also yield rc=1, so the error text ("boom") pins that the
    guard path in _process_one actually ran and recorded the exception."""
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    out_file = tmp_path / "apply.json"
    register_all()
    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=RuntimeError("boom"),
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
    # (a) crashed worker => non-zero exit, never silent success.
    assert rc != 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    workspaces = [ws for repo in payload["repos"] for ws in repo["workspaces"]]
    assert workspaces, "expected the crashed workspace to be reported"
    # (b) the affected workspace is reported fail/not-clean, with the original
    # exception preserved (guard path, not the generic fallthrough string).
    assert all(ws["status"] == "fail" for ws in workspaces)
    assert any("boom" in (ws["error"] or "") for ws in workspaces)


def test_apply_log_file_captures_transcript(tmp_path: Path, init_git_repo) -> None:
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
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
    assert "Summary" in transcript
    assert "workspaces fail" in transcript


def test_plan_log_file_includes_footer(tmp_path: Path, capsys, init_git_repo) -> None:
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
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


def test_apply_dirty_skip_does_not_scan_actions(tmp_path: Path, init_git_repo) -> None:
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - uses: actions/checkout@v3\n",
        encoding="utf-8",
    )
    register_all()
    scan_calls: list[tuple[Path, bool]] = []

    def track_scan(repo: Path, *, apply: bool = False):
        scan_calls.append((repo, apply))
        return [], 0

    with (
        patch(
            "repo_ops_upgrade.run.orchestration.process_workspace",
            side_effect=_dirty_skip,
        ),
        patch("repo_ops_upgrade.run.actions.scan_actions", side_effect=track_scan),
    ):
        main_apply([str(tmp_path), "--flat", "--quiet"])

    assert scan_calls == []


def test_strict_exit_counts_dirty_skip(tmp_path: Path, init_git_repo) -> None:
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_dirty_skip,
    ):
        rc_normal = main_apply([str(tmp_path), "--flat", "--skip-actions", "--quiet"])
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


def test_apply_snapshots_repo_dirty_state_once_per_bundle(tmp_path: Path, init_git_repo) -> None:
    """Two sibling workspaces in the same repo must share one dirty snapshot,
    not each re-check the live git tree — otherwise an earlier sibling's own
    applied-but-uncommitted changes make every later sibling look dirty and get
    skipped outright, even though the repo was clean at run-start. Issue #152.
    """
    _npm_workspace(tmp_path, init_git_repo=init_git_repo)
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    register_all()

    calls: list[dict] = []

    def _record(wr: WorkspaceResult, **kwargs) -> WorkspaceResult:
        calls.append(kwargs)
        wr.status = "ok"
        return wr

    with (
        patch(
            "repo_ops_upgrade.run.orchestration.process_workspace",
            side_effect=_record,
        ),
        patch("repo_ops_upgrade.run.is_git_dirty", return_value=False) as mock_dirty,
    ):
        rc = main_apply([str(tmp_path), "--flat", "--skip-actions", "--quiet"])

    assert rc == 0
    assert len(calls) == 2  # npm root workspace + precommit workspace
    # One live dirty check per repo bundle, not one per workspace.
    assert mock_dirty.call_count == 1
    # Both sibling workspaces receive the same snapshotted value.
    assert all(c["repo_started_dirty"] is False for c in calls)


def test_upgrade_parser_exposes_log_file_and_strict_exit() -> None:
    from repo_ops_core.cli import build_upgrade_parser

    for apply in (False, True):
        parser = build_upgrade_parser(apply=apply)
        dests = {a.dest for a in parser._actions}
        assert "log_file" in dests
        assert "strict_exit" in dests
        assert "max_failures" in dests
