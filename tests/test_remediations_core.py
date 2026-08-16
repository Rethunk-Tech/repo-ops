"""Tests for the ecosystem-agnostic remediation framework (core)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_core import remediations as core


def _result(tmp_path: Path, make_workspace, ecosystem: str = "node") -> WorkspaceResult:
    return WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem=ecosystem, manager="npm", label="test")
    )


# ── RemediationHook dataclass ──────────────────────────────────────────────


def test_hook_fields_accessible(tmp_path: Path) -> None:
    hook = core.RemediationHook(
        id="test_hook",
        detect=lambda ws, ctx: True,
        report=lambda ws, ctx: ["would do something"],
        apply=lambda ws, ctx: (True, "done"),
    )
    assert hook.id == "test_hook"
    assert hook.files is None  # default


def test_hook_with_files_callable(tmp_path: Path, make_workspace) -> None:
    hook = core.RemediationHook(
        id="files_hook",
        detect=lambda ws, ctx: True,
        report=lambda ws, ctx: [],
        apply=lambda ws, ctx: (True, "ok"),
        files=lambda ws: ["some/file.json"],
    )
    assert hook.files is not None
    assert hook.files(make_workspace(tmp_path, ecosystem="node", manager="npm", label="test")) == [
        "some/file.json"
    ]


# ── run_hooks: plan mode ───────────────────────────────────────────────────


def test_run_hooks_plan_records_pending(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "h",
        lambda ws, ctx: True,
        lambda ws, ctx: ["REMEDIATE h: would do X"],
        lambda ws, ctx: (True, "X done"),
    )
    core.run_hooks([hook], result, apply=False, skip=set())
    assert len(result.remediations) == 1
    assert result.remediations[0]["status"] == "pending"
    assert "would do X" in result.remediations[0]["detail"]


def test_run_hooks_apply_records_applied(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "h",
        lambda ws, ctx: True,
        lambda ws, ctx: [],
        lambda ws, ctx: (True, "X done (ok)"),
    )
    core.run_hooks([hook], result, apply=True, skip=set())
    assert result.remediations[0]["status"] == "applied"
    assert "(ok)" in result.remediations[0]["detail"]


def test_run_hooks_apply_fail_sets_result_fail(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "h",
        lambda ws, ctx: True,
        lambda ws, ctx: [],
        lambda ws, ctx: (False, "X failed"),
    )
    core.run_hooks([hook], result, apply=True, skip=set())
    assert result.remediations[0]["status"] == "fail"
    assert result.status == "fail"


def test_run_hooks_no_detect_no_entry(tmp_path: Path, make_workspace) -> None:
    """Hooks that don't detect produce no remediation entry (status ok is suppressed)."""
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "h",
        lambda ws, ctx: False,
        lambda ws, ctx: [],
        lambda ws, ctx: (True, ""),
    )
    core.run_hooks([hook], result, apply=True, skip=set())
    assert result.remediations == []


def test_run_hooks_skip_set_bypasses_hook(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "skip_me",
        lambda ws, ctx: True,
        lambda ws, ctx: ["should not appear"],
        lambda ws, ctx: (True, ""),
    )
    core.run_hooks([hook], result, apply=False, skip={"skip_me"})
    assert result.remediations == []


def test_run_hooks_exception_caught_per_hook(tmp_path: Path, make_workspace) -> None:
    """An exception in detect is caught; subsequent hooks still run."""
    result = _result(tmp_path, make_workspace=make_workspace)
    bad = core.RemediationHook(
        "bad",
        lambda ws, ctx: (_ for _ in ()).throw(RuntimeError("boom")),
        lambda ws, ctx: [],
        lambda ws, ctx: (True, ""),
    )
    good = core.RemediationHook(
        "good",
        lambda ws, ctx: True,
        lambda ws, ctx: ["would do good"],
        lambda ws, ctx: (True, ""),
    )
    core.run_hooks([bad, good], result, apply=False, skip=set())
    ids = [e["id"] for e in result.remediations]
    assert "bad" in ids
    assert result.remediations[0]["status"] == "fail"
    assert "good" in ids


def test_run_hooks_files_included_in_entry(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "h",
        lambda ws, ctx: True,
        lambda ws, ctx: [],
        lambda ws, ctx: (True, "ok"),
        files=lambda ws: ["biome.json"],
    )
    core.run_hooks([hook], result, apply=True, skip=set())
    assert result.remediations[0].get("files") == ["biome.json"]


def test_run_hooks_empty_files_not_included(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "h",
        lambda ws, ctx: True,
        lambda ws, ctx: [],
        lambda ws, ctx: (True, "ok"),
        files=lambda ws: [],  # empty — should not appear in entry
    )
    core.run_hooks([hook], result, apply=True, skip=set())
    assert "files" not in result.remediations[0]


# ── dispatch: global registry integration ─────────────────────────────────


def test_dispatch_uses_provided_registry(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "custom",
        lambda ws, ctx: True,
        lambda ws, ctx: ["plan line"],
        lambda ws, ctx: (True, "applied"),
    )
    core.dispatch(result, apply=False, skip=set(), _registry=[hook])
    assert result.remediations[0]["id"] == "custom"
    assert result.remediations[0]["status"] == "pending"


def test_dispatch_empty_registry_no_op(tmp_path: Path, make_workspace) -> None:
    result = _result(tmp_path, make_workspace=make_workspace)
    core.dispatch(result, apply=True, skip=set(), _registry=[])
    assert result.remediations == []
    assert result.status == "ok"


def test_dispatch_global_registry_populated(tmp_path: Path) -> None:
    """After the ecosystem packages load, the registry has unique expected hooks."""
    core.load_ecosystem_hooks()

    ids = [h.id for h in core._GLOBAL_REGISTRY]
    expected = {
        "biome_schema",
        "playwright_browsers",
        "codegen",
        "package_manager_pin",
        "ruff_fix",
        "gofmt",
        "go_generate",
        "cargo_fix",
        "deno_fix",
    }
    assert expected.issubset(set(ids)), f"missing hooks: {expected - set(ids)}"
    # Hook ids must be unique after full orchestration import set.
    assert len(ids) == len(set(ids)), f"duplicate hook ids in registry: {ids}"


# ── VERBOSE flag observable through run_hooks ──────────────────────────────


def test_verbose_flag_observed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_workspace
) -> None:
    from repo_ops_core import process

    result = _result(tmp_path, make_workspace=make_workspace)
    hook = core.RemediationHook(
        "verbose_hook",
        lambda ws, ctx: True,
        lambda ws, ctx: [],
        lambda ws, ctx: (True, "ok"),
    )
    process.set_verbose(True)
    try:
        core.run_hooks([hook], result, apply=True, skip=set())
    finally:
        process.set_verbose(False)

    captured = capsys.readouterr()
    assert "remediation verbose_hook: detect=True" in captured.err
