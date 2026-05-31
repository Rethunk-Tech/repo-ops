"""Plan/check must not invoke corepack_prepare."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade import registry


def test_check_workspace_does_not_call_corepack_prepare(tmp_path: Path):
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="",
    )

    def fake_checker(_ws: Workspace):
        return []

    with (
        patch("repo_ops_upgrade.registry.corepack_prepare") as mock_prepare,
        patch.dict(
            registry.CHECKERS,
            {"pnpm": fake_checker},
            clear=False,
        ),
    ):
        result = registry.check_workspace(ws)

    mock_prepare.assert_not_called()
    assert result.status == "ok"


def test_apply_workspace_still_calls_corepack_prepare(tmp_path: Path):
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="",
    )

    with (
        patch("repo_ops_upgrade.registry.corepack_prepare") as mock_prepare,
        patch.dict(registry.APPLIERS, {"pnpm": lambda _ws: None}, clear=False),
    ):
        registry.apply_workspace(ws)

    mock_prepare.assert_called_once_with("pnpm", tmp_path)
