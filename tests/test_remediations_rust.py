"""Tests for Rust-ecosystem remediation hooks (cargo_fix / cargo fmt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_rust import remediations as rust_rem


def _result(tmp_path: Path, make_workspace, ecosystem: str = "rust") -> WorkspaceResult:
    return WorkspaceResult(
        workspace=make_workspace(
            tmp_path, ecosystem=ecosystem, manager="cargo", label="test"
        )
    )


# ── detect_cargo_fix ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ecosystem,write_cargo_toml,has_change,apply,expected",
    [
        ("node", False, True, True, False),
        ("go", True, True, True, False),
        ("py", False, True, True, False),
        ("rust", False, True, True, False),
        ("rust", True, True, True, True),
        # No changes AND apply=True → don't trigger (nothing changed to reformat).
        ("rust", True, False, True, False),
        # Plan mode (apply=False) with empty changes still triggers for report.
        ("rust", True, False, False, True),
    ],
    ids=[
        "wrong-ecosystem-node",
        "wrong-ecosystem-go-with-cargo-toml",
        "wrong-ecosystem-py",
        "rust-no-cargo-toml",
        "rust-with-cargo-toml-and-changes",
        "no-changes-apply-mode",
        "plan-mode-no-changes",
    ],
)
def test_detect_cargo_fix(
    tmp_path: Path,
    make_workspace,
    make_change,
    ecosystem: str,
    write_cargo_toml: bool,
    has_change: bool,
    apply: bool,
    expected: bool,
) -> None:
    if write_cargo_toml:
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
    ws = make_workspace(tmp_path, ecosystem=ecosystem, manager="cargo", label="test")
    changes = [make_change("serde")] if has_change else []
    ctx: dict[str, Any] = {"changes": changes, "apply": apply}
    assert rust_rem.detect_cargo_fix(ws, ctx) is expected


# ── report_cargo_fix ─────────────────────────────────────────────────────────


def test_report_cargo_fix_returns_useful_message(
    tmp_path: Path, make_workspace
) -> None:
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


def test_apply_cargo_fix_failure_captures_stderr(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stderr="error: could not find `Cargo.toml`")

    with patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = rust_rem.apply_cargo_fix(ws, ctx)

    assert ok is False
    assert "cargo fmt" in detail
    assert "Cargo.toml" in detail


def test_apply_cargo_fix_uses_stdout_when_stderr_empty(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="test")
    ctx: dict[str, Any] = {}
    fail_proc = make_proc(1, stdout="some stdout error", stderr="")

    with patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run:
        mock_run.return_value = fail_proc
        ok, detail = rust_rem.apply_cargo_fix(ws, ctx)

    assert ok is False
    assert "some stdout error" in detail


# ── run_remediations integration ─────────────────────────────────────────────


def test_run_remediations_plan_mode(
    tmp_path: Path, make_workspace, make_change
) -> None:
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


def test_run_remediations_apply_mode(
    tmp_path: Path, make_workspace, make_change, make_proc
) -> None:
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


def test_cargo_fix_skipped_in_skip_set(
    tmp_path: Path, make_workspace, make_change
) -> None:
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
