"""Tests for Python-ecosystem remediation hooks (ruff_fix)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade_remediations_py import remediations as py_rem


def _ws(tmp_path: Path, ecosystem: str = "py") -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem=ecosystem,
        manager="uv",
        git_repo=tmp_path,
        label="test",
    )


def _result(tmp_path: Path, ecosystem: str = "py") -> WorkspaceResult:
    return WorkspaceResult(workspace=_ws(tmp_path, ecosystem))


def _fake_change(name: str) -> Any:
    obj = MagicMock()
    obj.name = name
    return obj


# ── detect_ruff ─────────────────────────────────────────────────────────────


def test_detect_ruff_false_for_node_ecosystem(tmp_path: Path) -> None:
    ws = _ws(tmp_path, ecosystem="node")
    ctx: dict[str, Any] = {"changes": [_fake_change("react")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_false_for_go_ecosystem(tmp_path: Path) -> None:
    ws = _ws(tmp_path, ecosystem="go")
    ctx: dict[str, Any] = {"changes": [_fake_change("golang.org/x/net")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_false_python_no_ruff_config(tmp_path: Path) -> None:
    # python workspace but no ruff.toml / [tool.ruff]
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [_fake_change("httpx")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_true_via_ruff_toml(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [_fake_change("httpx")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_true_via_dot_ruff_toml(tmp_path: Path) -> None:
    (tmp_path / ".ruff.toml").write_text("line-length = 88\n")
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [_fake_change("httpx")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_true_via_pyproject_tool_ruff(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'foo'\n\n[tool.ruff]\nline-length = 88\n"
    )
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [_fake_change("httpx")], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is True


def test_detect_ruff_false_when_no_changes(tmp_path: Path) -> None:
    """No changes AND apply=True → don't trigger (nothing changed to reformat)."""
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    assert py_rem.detect_ruff(ws, ctx) is False


def test_detect_ruff_true_plan_mode_no_changes(tmp_path: Path) -> None:
    """Plan mode (apply=False) with empty changes still triggers for report."""
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    assert py_rem.detect_ruff(ws, ctx) is True


# ── report_ruff ──────────────────────────────────────────────────────────────


def test_report_ruff_returns_useful_message(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    lines = py_rem.report_ruff(ws, ctx)
    assert len(lines) == 1
    assert "ruff" in lines[0]
    assert "REMEDIATE" in lines[0]


# ── apply_ruff ───────────────────────────────────────────────────────────────


def _make_proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_apply_ruff_success(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    fix_ok = _make_proc(0, stdout="All checks passed")
    fmt_ok = _make_proc(0, stdout="reformatted 0 files")

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


def test_apply_ruff_check_fails(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {}
    fix_fail = _make_proc(1, stderr="E501 line too long")
    fmt_ok = _make_proc(0)

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_fail, fmt_ok]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is False
    assert "check --fix" in detail
    assert "E501" in detail


def test_apply_ruff_format_fails(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {}
    fix_ok = _make_proc(0)
    fmt_fail = _make_proc(1, stderr="format error")

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_ok, fmt_fail]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is False
    assert "format" in detail


def test_apply_ruff_both_fail_detail_covers_both(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    ctx: dict[str, Any] = {}
    fix_fail = _make_proc(1, stderr="lint error")
    fmt_fail = _make_proc(1, stderr="format error")

    with patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run:
        mock_run.side_effect = [fix_fail, fmt_fail]
        ok, detail = py_rem.apply_ruff(ws, ctx)

    assert ok is False
    assert "check --fix" in detail
    assert "format" in detail


# ── run_remediations integration (via run_hooks) ─────────────────────────────


def test_run_remediations_plan_mode(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    result = _result(tmp_path)
    result.changes = [_fake_change("httpx")]

    fake = py_rem.RemediationHook(
        "ruff_fix",
        py_rem.detect_ruff,
        py_rem.report_ruff,
        py_rem.apply_ruff,
    )
    with patch.object(py_rem, "REMEDIATIONS", [fake]):
        py_rem.run_remediations(result, apply=False, skip=set())

    assert len(result.remediations) == 1
    assert result.remediations[0]["status"] == "pending"
    assert "REMEDIATE ruff" in result.remediations[0]["detail"]


def test_run_remediations_apply_mode(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 88\n")
    result = _result(tmp_path)
    result.changes = [_fake_change("httpx")]

    fix_ok = _make_proc(0)
    fmt_ok = _make_proc(0)

    fake = py_rem.RemediationHook(
        "ruff_fix",
        py_rem.detect_ruff,
        py_rem.report_ruff,
        py_rem.apply_ruff,
    )
    with (
        patch.object(py_rem, "REMEDIATIONS", [fake]),
        patch("repo_ops_upgrade_remediations_py.remediations.run") as mock_run,
    ):
        mock_run.side_effect = [fix_ok, fmt_ok]
        py_rem.run_remediations(result, apply=True, skip=set())

    assert result.remediations[0]["status"] == "applied"
    assert result.status == "ok"


def test_ruff_skipped_in_skip_set(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("")
    result = _result(tmp_path)
    result.changes = [_fake_change("httpx")]

    fake = py_rem.RemediationHook(
        "ruff_fix",
        py_rem.detect_ruff,
        py_rem.report_ruff,
        py_rem.apply_ruff,
    )
    with patch.object(py_rem, "REMEDIATIONS", [fake]):
        py_rem.run_remediations(result, apply=True, skip={"ruff_fix"})

    assert result.remediations == []
