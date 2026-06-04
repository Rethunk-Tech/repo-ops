"""Remediation plan vs apply reporting (issue #11)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core import process
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade_remediations_js import remediations


def _sample_workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="test",
    )


def test_run_remediations_plan_vs_apply_detail(tmp_path: Path) -> None:
    ws = _sample_workspace(tmp_path)
    result = WorkspaceResult(workspace=ws)
    fake = remediations.Remediation(
        "playwright_browsers",
        lambda _ws, _ctx: True,
        lambda ws, _ctx: [
            f"REMEDIATE playwright: would run {ws.manager} exec playwright install"
        ],
        lambda ws, _ctx: (True, f"{ws.manager} exec playwright install (ok)"),
    )
    with patch.object(remediations, "REMEDIATIONS", [fake]):
        remediations.run_remediations(result, apply=False, skip=set())
    assert result.remediations[0]["status"] == "pending"
    assert "would run" in result.remediations[0]["detail"]

    result2 = WorkspaceResult(workspace=ws)
    with patch.object(remediations, "REMEDIATIONS", [fake]):
        remediations.run_remediations(result2, apply=True, skip=set())
    assert result2.remediations[0]["status"] == "applied"
    assert "would run" not in result2.remediations[0]["detail"]
    assert "(ok)" in result2.remediations[0]["detail"]


def test_verbose_flag_observed_live(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """process.VERBOSE rebind is visible to run_remediations (issue #68).

    A by-value import of VERBOSE would freeze it at False; the module-level
    reference fix (process.VERBOSE) must read the live global.
    """
    ws = _sample_workspace(tmp_path)
    result = WorkspaceResult(workspace=ws)
    fake = remediations.Remediation(
        "biome_schema",
        lambda _ws, _ctx: True,
        lambda _ws, _ctx: [],
        lambda _ws, _ctx: (True, "ok"),
    )
    process.set_verbose(True)
    try:
        with patch.object(remediations, "REMEDIATIONS", [fake]):
            remediations.run_remediations(result, apply=True, skip=set())
    finally:
        process.set_verbose(False)

    captured = capsys.readouterr()
    assert "remediation biome_schema: detect=True" in captured.err
