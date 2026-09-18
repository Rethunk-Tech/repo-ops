"""Streaming per-workspace progress lines emitted to stderr."""

from __future__ import annotations

import concurrent.futures
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade.orchestration import _emit_progress, _format_elapsed


def _make_ws(tmp_path: Path, *, label: str = "my-repo", manager: str = "npm") -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager=manager,
        git_repo=tmp_path,
        label=label,
    )


def test_format_elapsed_under_one_minute() -> None:
    assert _format_elapsed(0.0) == "0.0s"
    assert _format_elapsed(1.23) == "1.2s"
    assert _format_elapsed(59.94) == "59.9s"


def test_format_elapsed_one_minute_and_above() -> None:
    assert _format_elapsed(60.0) == "1m 0s"
    assert _format_elapsed(83.4) == "1m 23s"
    assert _format_elapsed(125.6) == "2m 6s"


def test_emit_progress_ok_workspace(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path, label="my-repo", manager="npm")
    result = WorkspaceResult(workspace=ws, status="ok")

    buf = StringIO()
    with patch.object(sys, "stderr", buf):
        _emit_progress(result, elapsed_s=1.23)

    line = buf.getvalue().strip()
    assert "[done]" in line
    assert "my-repo" in line
    assert "npm" in line
    assert "0 change(s)" in line
    assert "in 1.2s" in line
    assert "FAIL" not in line


def test_emit_progress_includes_minute_duration(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path, label="slow-repo", manager="yarn")
    result = WorkspaceResult(workspace=ws, status="ok")

    buf = StringIO()
    with patch.object(sys, "stderr", buf):
        _emit_progress(result, elapsed_s=125.6)

    line = buf.getvalue().strip()
    assert line == "[done] slow-repo (yarn) — 0 change(s) in 2m 6s"


def test_emit_progress_fail_workspace(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path, label="my-repo", manager="yarn")
    result = WorkspaceResult(workspace=ws, status="fail", error="build failed: exit 1")

    buf = StringIO()
    with patch.object(sys, "stderr", buf):
        _emit_progress(result, elapsed_s=0.5)

    line = buf.getvalue().strip()
    assert "[done]" in line
    assert "in 0.5s" in line
    assert "FAIL: build failed: exit 1" in line


def test_emit_progress_fail_multiline_error_uses_first_line(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    result = WorkspaceResult(
        workspace=ws,
        status="fail",
        error="first line\nsecond line\nthird line",
    )

    buf = StringIO()
    with patch.object(sys, "stderr", buf):
        _emit_progress(result, elapsed_s=0.1)

    line = buf.getvalue().strip()
    assert "FAIL: first line" in line
    assert "second line" not in line


def test_emit_progress_suppressed_by_env_var(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    buf = StringIO()
    with (
        patch.object(sys, "stderr", buf),
        patch.dict(os.environ, {"REPO_OPS_NO_PROGRESS": "1"}),
    ):
        _emit_progress(result, elapsed_s=0.1)

    assert buf.getvalue() == ""


def test_emit_progress_not_suppressed_when_env_var_absent(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    buf = StringIO()
    env = {k: v for k, v in os.environ.items() if k != "REPO_OPS_NO_PROGRESS"}
    with (
        patch.object(sys, "stderr", buf),
        patch.dict(os.environ, env, clear=True),
    ):
        _emit_progress(result, elapsed_s=0.1)

    assert buf.getvalue().strip() != ""


def test_emit_progress_no_torn_lines_under_parallelism(tmp_path: Path) -> None:
    """Concurrent _emit_progress calls must each produce exactly one complete line."""
    import threading

    ws = _make_ws(tmp_path)
    results = [WorkspaceResult(workspace=ws, status="ok") for _ in range(20)]

    lines: list[str] = []
    # Separate lock so we don't re-enter _PROGRESS_LOCK (which _emit_progress holds
    # while calling print, so patching print and re-acquiring _PROGRESS_LOCK = deadlock).
    _collect_lock = threading.Lock()

    def capturing_print(*args, file=None, **kwargs):
        if file is sys.stderr:
            with _collect_lock:
                lines.append(str(args[0]) if args else "")

    env = {k: v for k, v in os.environ.items() if k != "REPO_OPS_NO_PROGRESS"}
    with (
        patch("repo_ops_upgrade.orchestration.print", side_effect=capturing_print),
        patch.dict(os.environ, env, clear=True),
        concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool,
    ):
        list(pool.map(lambda r: _emit_progress(r, elapsed_s=0.01), results))

    assert len(lines) == 20
    for line in lines:
        assert "[done]" in line


def test_process_workspace_sets_elapsed_s(tmp_path: Path) -> None:
    """process_workspace must populate elapsed_s on the returned WorkspaceResult."""
    from unittest.mock import patch as _patch

    from repo_ops_upgrade.orchestration import process_workspace

    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    def _fake_check(workspace):
        return WorkspaceResult(workspace=workspace, status="ok")

    with (
        _patch("repo_ops_upgrade.orchestration.should_exclude", return_value=False),
        _patch(
            "repo_ops_upgrade.orchestration.ecosystems.check_workspace",
            side_effect=_fake_check,
        ),
        _patch(
            "repo_ops_upgrade.orchestration.remediations.run_remediations",
        ),
        patch.dict(os.environ, {"REPO_OPS_NO_PROGRESS": "1"}),
    ):
        out = process_workspace(
            result,
            do_apply=False,
            do_verify=False,
            allow_dirty=False,
        )

    assert out.elapsed_s is not None
    assert out.elapsed_s >= 0.0


def test_workspace_result_to_json_includes_elapsed_s(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws, elapsed_s=2.567)
    data = result.to_json()
    assert data["elapsed_s"] == 2.57  # rounded to 2 decimal places


def test_workspace_result_to_json_omits_elapsed_s_when_none(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)
    data = result.to_json()
    assert "elapsed_s" not in data
