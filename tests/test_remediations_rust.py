"""Tests for Rust-ecosystem remediation hooks (cargo_fix / cargo fmt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_rust import remediations as rust_rem


def _result(tmp_path: Path, make_workspace, ecosystem: str = "rust") -> WorkspaceResult:
    return WorkspaceResult(workspace=make_workspace(tmp_path, ecosystem=ecosystem, manager="cargo", label="test"))


# ── detect_cargo_fix ─────────────────────────────────────────────────────────


def test_detect_cargo_fix_false_for_node_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("react")], "apply": True}
    assert rust_rem.detect_cargo_fix(ws, ctx) is False


def test_detect_cargo_fix_false_for_go_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="go", manager="cargo", label="test")
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}
    assert rust_rem.detect_cargo_fix(ws, ctx) is False


def test_detect_cargo_fix_false_for_py_ecosystem(tmp_path: Path, make_workspace, make_change) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("httpx")], "apply": True}
    assert rust_rem.detect_cargo_fix(ws, ctx) is False


def test_detect_cargo_fix_false_rust_no_cargo_toml(tmp_path: Path, make_workspace, make_change) -> None:
    # rust ecosystem but no Cargo.toml present
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("serde")], "apply": True}
    assert rust_rem.detect_cargo_fix(ws, ctx) is False


def test_detect_cargo_fix_true_rust_with_cargo_toml_and_changes(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\nversion = '0.1.0'\n")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [make_change("serde")], "apply": True}
    assert rust_rem.detect_cargo_fix(ws, ctx) is True


def test_detect_cargo_fix_false_when_no_changes_apply_mode(tmp_path: Path, make_workspace) -> None:
    """No changes AND apply=True → don't trigger (nothing changed to reformat)."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    assert rust_rem.detect_cargo_fix(ws, ctx) is False


def test_detect_cargo_fix_true_plan_mode_no_changes(tmp_path: Path, make_workspace) -> None:
    """Plan mode (apply=False) with empty changes still triggers for report."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    assert rust_rem.detect_cargo_fix(ws, ctx) is True


# ── report_cargo_fix ─────────────────────────────────────────────────────────


def test_report_cargo_fix_returns_useful_message(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": False}
    lines = rust_rem.report_cargo_fix(ws, ctx)
    assert len(lines) == 1
    assert "cargo_fix" in lines[0]
    assert "REMEDIATE" in lines[0]


# ── apply_cargo_fix ──────────────────────────────────────────────────────────


def test_apply_cargo_fix_success(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {"changes": [], "apply": True}
    ok_proc = make_proc(0)

    with patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run:
        mock_run.return_value = ok_proc
        ok, detail = rust_rem.apply_cargo_fix(ws, ctx)

    assert ok is True
    assert "(ok)" in detail
    cmd = mock_run.call_args[0][0]
    assert cmd == ["cargo", "fmt"]


def test_apply_cargo_fix_failure_captures_stderr(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stderr="error: could not find `Cargo.toml`")

    with patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = rust_rem.apply_cargo_fix(ws, ctx)

    assert ok is False
    assert "cargo fmt" in detail
    assert "Cargo.toml" in detail


def test_apply_cargo_fix_uses_stdout_when_stderr_empty(tmp_path: Path, make_workspace, make_proc) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stdout="some stdout error", stderr="")

    with patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = rust_rem.apply_cargo_fix(ws, ctx)

    assert ok is False
    assert "some stdout error" in detail


# ── run_remediations integration ─────────────────────────────────────────────


def test_run_remediations_plan_mode(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("serde")]

    fake = rust_rem.RemediationHook(
        "cargo_fix",
        rust_rem.detect_cargo_fix,
        rust_rem.report_cargo_fix,
        rust_rem.apply_cargo_fix,
    )
    with patch.object(rust_rem, "REMEDIATIONS", [fake]):
        rust_rem.run_remediations(result, apply=False, skip=set())

    assert len(result.remediations) == 1
    assert result.remediations[0]["status"] == "pending"
    assert "REMEDIATE cargo_fix" in result.remediations[0]["detail"]


def test_run_remediations_apply_mode(tmp_path: Path, make_workspace, make_change, make_proc) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("serde")]

    ok_proc = make_proc(0)
    fake = rust_rem.RemediationHook(
        "cargo_fix",
        rust_rem.detect_cargo_fix,
        rust_rem.report_cargo_fix,
        rust_rem.apply_cargo_fix,
    )
    with (
        patch.object(rust_rem, "REMEDIATIONS", [fake]),
        patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run,
    ):
        mock_run.return_value = ok_proc
        rust_rem.run_remediations(result, apply=True, skip=set())

    assert result.remediations[0]["status"] == "applied"
    assert result.status == "ok"


def test_cargo_fix_skipped_in_skip_set(tmp_path: Path, make_workspace, make_change) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    result = _result(tmp_path, make_workspace=make_workspace)
    result.changes = [make_change("serde")]

    fake = rust_rem.RemediationHook(
        "cargo_fix",
        rust_rem.detect_cargo_fix,
        rust_rem.report_cargo_fix,
        rust_rem.apply_cargo_fix,
    )
    with patch.object(rust_rem, "REMEDIATIONS", [fake]):
        rust_rem.run_remediations(result, apply=True, skip={"cargo_fix"})

    assert result.remediations == []
