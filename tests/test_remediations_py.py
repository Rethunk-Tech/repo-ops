"""Tests for Python-ecosystem remediation hooks (ruff_fix)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_py import remediations as py_rem


def _result(tmp_path: Path, make_workspace, ecosystem: str = "py") -> WorkspaceResult:
    return WorkspaceResult(
        workspace=make_workspace(
            tmp_path, ecosystem=ecosystem, manager="uv", label="test"
        )
    )


# ── detect_ruff ─────────────────────────────────────────────────────────────


def test_detect_ruff_false_for_node_ecosystem(
    tmp_path: Path, make_workspace, make_change
) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("react")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_false_for_go_ecosystem(
    tmp_path: Path, make_workspace, make_change
) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("golang.org/x/net")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_false_python_no_ruff_config(
    tmp_path: Path, make_workspace, make_change
) -> None:
    # python workspace but no ruff.toml / [tool.ruff]
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_true_via_ruff_toml(
    tmp_path: Path, make_workspace, make_change
) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    with patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"):
        assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_true_via_dot_ruff_toml(
    tmp_path: Path, make_workspace, make_change
) -> None:
    (tmp_path / ".ruff.toml").write_text("line-length = 88\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    with patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"):
        assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_true_via_pyproject_tool_ruff(
    tmp_path: Path, make_workspace, make_change
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'foo'\n\n[tool.ruff]\nline-length = 88\n"
    )
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    with patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"):
        assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_false_when_no_changes(tmp_path: Path, make_workspace) -> None:
    """No changes AND apply=True → don't trigger (nothing changed to reformat)."""
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    with patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"):
        assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_true_plan_mode_no_changes(tmp_path: Path, make_workspace) -> None:
    """Plan mode (apply=False) with empty changes still triggers for report."""
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    with patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"):
        assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_false_when_ruff_absent(
    tmp_path: Path, make_workspace, make_change
) -> None:
    """Gracefully skip detection when the ruff binary is not installed."""
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    with patch.object(py_rem.shutil, "which", return_value=None):
        assert py_rem.detect_ruff(ws, ctx) is False


# ── report_ruff ──────────────────────────────────────────────────────────────


def test_report_ruff_returns_useful_message(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    lines = py_rem.report_ruff(ws, ctx)
    assert len(lines) == 1
    assert "ruff" in lines[0]
    assert "REMEDIATE" in lines[0]


# ── apply_ruff ───────────────────────────────────────────────────────────────


def test_apply_ruff_success(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    fix_ok = make_proc(0, stdout="All checks passed")
    fmt_ok = make_proc(0, stdout="reformatted 0 files")

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_ok, fmt_ok]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is True
    assert "(ok)" in detail
    assert mock_run.call_count == 2
    # First call: ruff check --fix
    first_cmd = mock_run.call_args_list[0][0][0]
    assert "check" in first_cmd and "--fix" in first_cmd
    # Second call: ruff format
    second_cmd = mock_run.call_args_list[1][0][0]
    assert "format" in second_cmd


def test_apply_ruff_check_fails(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {}
    fix_fail = make_proc(1, stderr="E501 line too long")
    fmt_ok = make_proc(0)

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_fail, fmt_ok]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is False
    assert "check --fix" in detail
    assert "E501" in detail


def test_apply_ruff_format_fails(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {}
    fix_ok = make_proc(0)
    fmt_fail = make_proc(1, stderr="format error")

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_ok, fmt_fail]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is False
    assert "format" in detail


def test_apply_ruff_both_fail_detail_covers_both(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="uv", label="test")
    ctx: dict[str, Any] = {}
    fix_fail = make_proc(1, stderr="lint error")
    fmt_fail = make_proc(1, stderr="format error")

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_fail, fmt_fail]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is False
    assert "check --fix" in detail
    assert "format" in detail


# ── run_remediations integration (via run_hooks) ─────────────────────────────


def test_run_remediations_plan_mode(
    tmp_path: Path, make_workspace, make_change
) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("httpx")]

    fake = py_rem.RemediationHook(
        "ruff_fix",
        py_rem.detect_ruff,
        py_rem.report_ruff,
        py_rem.apply_ruff,
    )
    with (
        patch.object(py_rem, "REMEDIATIONS", [fake]),
        patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"),
    ):
        py_rem.run_remediations(result, apply=False, skip=set())

    assert len(result.remediations) == 1
    assert result.remediations[0]["status"] == "pending"
    assert "REMEDIATE ruff" in result.remediations[0]["detail"]


def test_run_remediations_apply_mode(
    tmp_path: Path, make_workspace, make_change, make_proc
) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("httpx")]

    fix_ok = make_proc(0)
    fmt_ok = make_proc(0)

    fake = py_rem.RemediationHook(
        "ruff_fix",
        py_rem.detect_ruff,
        py_rem.report_ruff,
        py_rem.apply_ruff,
    )
    with (
        patch.object(py_rem, "REMEDIATIONS", [fake]),
        patch.object(py_rem.shutil, "which", return_value="/usr/bin/ruff"),
        patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run,
    ):
        mock_run.side_effect = [fix_ok, fmt_ok]
        py_rem.run_remediations(result, apply=True, skip=set())

    assert result.remediations[0]["status"] == "applied"
    assert result.status == "ok"


def test_ruff_skipped_in_skip_set(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "ruff.toml").write_text("")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("httpx")]

    fake = py_rem.RemediationHook(
        "ruff_fix",
        py_rem.detect_ruff,
        py_rem.report_ruff,
        py_rem.apply_ruff,
    )
    with patch.object(py_rem, "REMEDIATIONS", [fake]):
        py_rem.run_remediations(result, apply=True, skip={"ruff_fix"})

    assert result.remediations == []
