"""Tests for Go-ecosystem remediation hooks (gofmt, go_generate)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_go import remediations as go_rem


def _result(tmp_path: Path, make_workspace, ecosystem: str = "go") -> WorkspaceResult:
    return WorkspaceResult(workspace=make_workspace(tmp_path, ecosystem=ecosystem, manager="go", label="test"))


# ── detect_gofmt ─────────────────────────────────────────────────────────────


def test_detect_gofmt_false_for_node_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("react")], "apply": True}
    assert go_rem.detect_gofmt(ws, ctx) is False


def test_detect_gofmt_false_for_rust_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="go", label="test")
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    ctx: dict[str, Any] = {"changes": [make_change("crate")], "apply": True}
    assert go_rem.detect_gofmt(ws, ctx) is False


def test_detect_gofmt_false_for_py_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    assert go_rem.detect_gofmt(ws, ctx) is False


def test_detect_gofmt_false_go_no_go_mod(tmp_path: Path, make_workspace, make_change) -> None:
    # go ecosystem but no go.mod present
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("golang.org/x/net")], "apply": True}
    assert go_rem.detect_gofmt(ws, ctx) is False


def test_detect_gofmt_true_go_with_go_mod_and_changes(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("golang.org/x/net")], "apply": True}
    assert go_rem.detect_gofmt(ws, ctx) is True


def test_detect_gofmt_false_when_no_changes_apply_mode(tmp_path: Path, make_workspace) -> None:
    """No changes AND apply=True → don't trigger (nothing changed to reformat)."""
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    assert go_rem.detect_gofmt(ws, ctx) is False


def test_detect_gofmt_true_plan_mode_no_changes(tmp_path: Path, make_workspace) -> None:
    """Plan mode (apply=False) with empty changes still triggers for report."""
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    assert go_rem.detect_gofmt(ws, ctx) is True


# ── report_gofmt ─────────────────────────────────────────────────────────────


def test_report_gofmt_returns_useful_message(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    lines = go_rem.report_gofmt(ws, ctx)
    assert len(lines) == 1
    assert "gofmt" in lines[0]
    assert "REMEDIATE" in lines[0]


# ── apply_gofmt ──────────────────────────────────────────────────────────────


def test_apply_gofmt_success(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    ok_proc = make_proc(0)

    with patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run:
        mock_run.return_value = ok_proc
        ok, detail = go_rem.apply_gofmt(ws, ctx)

    assert ok is True
    assert "(ok)" in detail
    cmd = mock_run.call_args[0][0]
    assert cmd == ["gofmt", "-w", "."]


def test_apply_gofmt_failure_captures_stderr(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stderr="cannot find package")

    with patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = go_rem.apply_gofmt(ws, ctx)

    assert ok is False
    assert "gofmt" in detail
    assert "cannot find package" in detail


# ── detect_go_generate ───────────────────────────────────────────────────────


def test_detect_go_generate_false_for_non_go(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}
    assert go_rem.detect_go_generate(ws, ctx) is False


def test_detect_go_generate_false_no_go_mod(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}
    assert go_rem.detect_go_generate(ws, ctx) is False


def test_detect_go_generate_false_no_generate_directive(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}
    assert go_rem.detect_go_generate(ws, ctx) is False


def test_detect_go_generate_true_with_directive(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "gen.go").write_text("package main\n//go:generate stringer -type=Foo\n")
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}
    assert go_rem.detect_go_generate(ws, ctx) is True


def test_detect_go_generate_false_no_changes_apply_mode(tmp_path: Path, make_workspace) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "gen.go").write_text("package main\n//go:generate stringer -type=Foo\n")
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    assert go_rem.detect_go_generate(ws, ctx) is False


# ── report_go_generate ───────────────────────────────────────────────────────


def test_report_go_generate_returns_useful_message(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    lines = go_rem.report_go_generate(ws, ctx)
    assert len(lines) == 1
    assert "go_generate" in lines[0]
    assert "REMEDIATE" in lines[0]


# ── apply_go_generate ────────────────────────────────────────────────────────


def test_apply_go_generate_success(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    ok_proc = make_proc(0)

    with patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run:
        mock_run.return_value = ok_proc
        ok, detail = go_rem.apply_go_generate(ws, ctx)

    assert ok is True
    assert "(ok)" in detail
    cmd = mock_run.call_args[0][0]
    assert cmd == ["go", "generate", "./..."]


def test_apply_go_generate_failure_captures_stderr(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stderr="stringer: command not found")

    with patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = go_rem.apply_go_generate(ws, ctx)

    assert ok is False
    assert "go generate" in detail
    assert "stringer" in detail


# ── run_remediations integration ─────────────────────────────────────────────


def test_run_remediations_plan_mode(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("golang.org/x/net")]

    fake = go_rem.RemediationHook(
        "gofmt",
        go_rem.detect_gofmt,
        go_rem.report_gofmt,
        go_rem.apply_gofmt,
    )
    with patch.object(go_rem, "REMEDIATIONS", [fake]):
        go_rem.run_remediations(result, apply=False, skip=set())

    assert len(result.remediations) == 1
    assert result.remediations[0]["status"] == "pending"
    assert "REMEDIATE gofmt" in result.remediations[0]["detail"]


def test_run_remediations_apply_mode(tmp_path: Path, make_workspace, make_change, make_proc) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("golang.org/x/net")]

    ok_proc = make_proc(0)
    fake = go_rem.RemediationHook(
        "gofmt",
        go_rem.detect_gofmt,
        go_rem.report_gofmt,
        go_rem.apply_gofmt,
    )
    with (
        patch.object(go_rem, "REMEDIATIONS", [fake]),
        patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run,
    ):
        mock_run.return_value = ok_proc
        go_rem.run_remediations(result, apply=True, skip=set())

    assert result.remediations[0]["status"] == "applied"
    assert result.status == "ok"


def test_gofmt_skipped_in_skip_set(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("golang.org/x/net")]

    fake = go_rem.RemediationHook(
        "gofmt",
        go_rem.detect_gofmt,
        go_rem.report_gofmt,
        go_rem.apply_gofmt,
    )
    with patch.object(go_rem, "REMEDIATIONS", [fake]):
        go_rem.run_remediations(result, apply=True, skip={"gofmt"})

    assert result.remediations == []


def test_go_generate_skipped_in_skip_set(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "gen.go").write_text("package main\n//go:generate stringer\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("golang.org/x/tools")]

    fake = go_rem.RemediationHook(
        "go_generate",
        go_rem.detect_go_generate,
        go_rem.report_go_generate,
        go_rem.apply_go_generate,
    )
    with patch.object(go_rem, "REMEDIATIONS", [fake]):
        go_rem.run_remediations(result, apply=True, skip={"go_generate"})

    assert result.remediations == []
