"""Plan/check must not invoke corepack_prepare."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change, Workspace
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


def test_check_workspace_three_tuple_sets_overrides_and_behind(tmp_path: Path) -> None:
    """#110: yarn's (changes, overrides_audit, behind_latest) 3-tuple is unpacked."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )

    def fake_checker(_ws: Workspace):
        return (
            [Change("next", "16.2.6", "16.2.7")],
            [{"package": "lodash", "kind": "override"}],
            [Change("@sentry/nextjs", "10.55.0", "10.56.0")],
        )

    with patch.dict(registry.CHECKERS, {"yarn": fake_checker}, clear=False):
        result = registry.check_workspace(ws)

    assert result.status == "ok"
    assert [c.name for c in result.changes] == ["next"]
    assert result.overrides_audit == [{"package": "lodash", "kind": "override"}]
    assert result.behind_latest == [Change("@sentry/nextjs", "10.55.0", "10.56.0")]


def test_apply_workspace_returns_applier_behind_latest(tmp_path: Path) -> None:
    """#110: apply_workspace surfaces the behind_latest list an applier returns."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )
    behind = [Change("@sentry/nextjs", "10.55.0", "10.56.0")]
    with (
        patch("repo_ops_upgrade.registry.corepack_prepare"),
        patch.dict(registry.APPLIERS, {"yarn": lambda _ws: behind}, clear=False),
    ):
        out = registry.apply_workspace(ws)
    assert out == behind


def test_check_workspace_records_checker_failure(tmp_path: Path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="",
    )

    def boom(_ws: Workspace):
        raise RuntimeError("pnpm outdated failed")

    with patch.dict(registry.CHECKERS, {"pnpm": boom}, clear=False):
        result = registry.check_workspace(ws)

    assert result.status == "fail"
    assert "pnpm outdated failed" in (result.error or "")
