"""Tests for Deno-ecosystem remediation hooks (deno_fix / deno fmt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_deno import remediations as deno_rem


def _result(tmp_path: Path, make_workspace, ecosystem: str = "deno") -> WorkspaceResult:
    return WorkspaceResult(workspace=make_workspace(tmp_path, ecosystem=ecosystem, manager="deno", label="test"))


# ── detect_deno_fix ──────────────────────────────────────────────────────────


def test_detect_deno_fix_false_for_node_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("react")], "apply": True}
    assert deno_rem.detect_deno_fix(ws, ctx) is False


def test_detect_deno_fix_false_for_go_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="deno", label="test")
    (tmp_path / "deno.json").write_text("{}\n")
    ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}
    assert deno_rem.detect_deno_fix(ws, ctx) is False


def test_detect_deno_fix_false_for_py_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    assert deno_rem.detect_deno_fix(ws, ctx) is False


def test_detect_deno_fix_false_deno_no_config(tmp_path: Path, make_workspace, make_change) -> None:
    # deno ecosystem but no deno.json/deno.jsonc/deno.lock present
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("std")], "apply": True}
    assert deno_rem.detect_deno_fix(ws, ctx) is False


def test_detect_deno_fix_true_deno_with_config_and_changes(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "deno.json").write_text("{}\n")
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("std")], "apply": True}
    with patch.object(deno_rem.shutil, "which", return_value="/usr/bin/deno"):
        assert deno_rem.detect_deno_fix(ws, ctx) is True


def test_detect_deno_fix_true_with_jsonc_config(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "deno.jsonc").write_text("{}\n")
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("std")], "apply": True}
    with patch.object(deno_rem.shutil, "which", return_value="/usr/bin/deno"):
        assert deno_rem.detect_deno_fix(ws, ctx) is True


def test_detect_deno_fix_false_when_no_changes_apply_mode(tmp_path: Path, make_workspace) -> None:
    """No changes AND apply=True → don't trigger (nothing changed to reformat)."""
    (tmp_path / "deno.json").write_text("{}\n")
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    with patch.object(deno_rem.shutil, "which", return_value="/usr/bin/deno"):
        assert deno_rem.detect_deno_fix(ws, ctx) is False


def test_detect_deno_fix_true_plan_mode_no_changes(tmp_path: Path, make_workspace) -> None:
    """Plan mode (apply=False) with empty changes still triggers for report."""
    (tmp_path / "deno.json").write_text("{}\n")
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    with patch.object(deno_rem.shutil, "which", return_value="/usr/bin/deno"):
        assert deno_rem.detect_deno_fix(ws, ctx) is True


def test_detect_deno_fix_false_when_deno_absent(tmp_path: Path, make_workspace, make_change) -> None:
    """Gracefully skip (not fail) detection when the deno binary is not installed."""
    (tmp_path / "deno.json").write_text("{}\n")
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("std")], "apply": True}
    with patch.object(deno_rem.shutil, "which", return_value=None):
        assert deno_rem.detect_deno_fix(ws, ctx) is False


# ── report_deno_fix ──────────────────────────────────────────────────────────


def test_report_deno_fix_returns_useful_message(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    lines = deno_rem.report_deno_fix(ws, ctx)
    assert len(lines) == 1
    assert "deno_fix" in lines[0]
    assert "REMEDIATE" in lines[0]


# ── apply_deno_fix ───────────────────────────────────────────────────────────


def test_apply_deno_fix_success(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    ok_proc = make_proc(0)

    with patch("repo_ops_upgrade_remediations_deno.remediations.run") as mock_run:
        mock_run.return_value = ok_proc
        ok, detail = deno_rem.apply_deno_fix(ws, ctx)

    assert ok is True
    assert "(ok)" in detail
    cmd = mock_run.call_args[0][0]
    assert cmd == ["deno", "fmt"]


def test_apply_deno_fix_failure_captures_stderr(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stderr="error: could not find config file")

    with patch("repo_ops_upgrade_remediations_deno.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = deno_rem.apply_deno_fix(ws, ctx)

    assert ok is False
    assert "deno fmt" in detail
    assert "could not find config file" in detail


def test_apply_deno_fix_uses_stdout_when_stderr_empty(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stdout="some stdout error", stderr="")

    with patch("repo_ops_upgrade_remediations_deno.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = deno_rem.apply_deno_fix(ws, ctx)

    assert ok is False
    assert "some stdout error" in detail


# ── run_remediations integration ─────────────────────────────────────────────


def test_run_remediations_plan_mode(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "deno.json").write_text("{}\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("std")]

    fake = deno_rem.RemediationHook(
        "deno_fix",
        deno_rem.detect_deno_fix,
        deno_rem.report_deno_fix,
        deno_rem.apply_deno_fix,
    )
    with (
        patch.object(deno_rem, "REMEDIATIONS", [fake]),
        patch.object(deno_rem.shutil, "which", return_value="/usr/bin/deno"),
    ):
        deno_rem.run_remediations(result, apply=False, skip=set())

    assert len(result.remediations) == 1
    assert result.remediations[0]["status"] == "pending"
    assert "REMEDIATE deno_fix" in result.remediations[0]["detail"]


def test_run_remediations_apply_mode(tmp_path: Path, make_workspace, make_change, make_proc) -> None:
    (tmp_path / "deno.json").write_text("{}\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("std")]

    ok_proc = make_proc(0)
    fake = deno_rem.RemediationHook(
        "deno_fix",
        deno_rem.detect_deno_fix,
        deno_rem.report_deno_fix,
        deno_rem.apply_deno_fix,
    )
    with (
        patch.object(deno_rem, "REMEDIATIONS", [fake]),
        patch.object(deno_rem.shutil, "which", return_value="/usr/bin/deno"),
        patch("repo_ops_upgrade_remediations_deno.remediations.run") as mock_run,
    ):
        mock_run.return_value = ok_proc
        deno_rem.run_remediations(result, apply=True, skip=set())

    assert result.remediations[0]["status"] == "applied"
    assert result.status == "ok"


def test_deno_fix_skipped_in_skip_set(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "deno.json").write_text("{}\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("std")]

    fake = deno_rem.RemediationHook(
        "deno_fix",
        deno_rem.detect_deno_fix,
        deno_rem.report_deno_fix,
        deno_rem.apply_deno_fix,
    )
    with patch.object(deno_rem, "REMEDIATIONS", [fake]):
        deno_rem.run_remediations(result, apply=True, skip={"deno_fix"})

    assert result.remediations == []
