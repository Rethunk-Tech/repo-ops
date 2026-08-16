"""Tests for Deno-ecosystem remediation hooks (deno_fix / deno fmt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_deno import remediations as deno_rem


def _result(tmp_path: Path, make_workspace, ecosystem: str = "deno") -> WorkspaceResult:
    return WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem=ecosystem, manager="deno", label="test")
    )


# ── detect_deno_fix ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ecosystem,config_file,has_change,apply,which_available,expected",
    [
        ("node", None, True, True, True, False),
        ("go", "deno.json", True, True, True, False),
        ("py", None, True, True, True, False),
        ("deno", None, True, True, True, False),
        ("deno", "deno.json", True, True, True, True),
        ("deno", "deno.jsonc", True, True, True, True),
        # No changes AND apply=True → don't trigger (nothing changed to reformat).
        ("deno", "deno.json", False, True, True, False),
        # Plan mode (apply=False) with empty changes still triggers for report.
        ("deno", "deno.json", False, False, True, True),
        # Gracefully skip (not fail) detection when the deno binary is absent.
        ("deno", "deno.json", True, True, False, False),
    ],
    ids=[
        "wrong-ecosystem-node",
        "wrong-ecosystem-go-with-config",
        "wrong-ecosystem-py",
        "deno-no-config",
        "deno-json-config",
        "deno-jsonc-config",
        "no-changes-apply-mode",
        "plan-mode-no-changes",
        "deno-absent",
    ],
)
def test_detect_deno_fix(
    tmp_path: Path,
    make_workspace,
    make_change,
    ecosystem: str,
    config_file: str | None,
    has_change: bool,
    apply: bool,
    which_available: bool,
    expected: bool,
) -> None:
    if config_file is not None:
        (tmp_path / config_file).write_text("{}\n")
    ws = make_workspace(tmp_path, ecosystem=ecosystem, manager="deno", label="test")
    changes = [make_change("std")] if has_change else []
    ctx: dict[str, Any] = {"changes": changes, "apply": apply}
    which_return = "/usr/bin/deno" if which_available else None
    with patch.object(deno_rem.shutil, "which", return_value=which_return):
        assert deno_rem.detect_deno_fix(ws, ctx) is expected


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


def test_apply_deno_fix_uses_stdout_when_stderr_empty(
    tmp_path: Path, make_workspace, make_proc
) -> None:
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


def test_run_remediations_apply_mode(
    tmp_path: Path, make_workspace, make_change, make_proc
) -> None:
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
