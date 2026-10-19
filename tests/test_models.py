"""Unit tests for repo_ops_models.models."""

from __future__ import annotations

from pathlib import Path

import repo_ops_models.models as models_mod
from repo_ops_models import (
    ActionBump,
    Change,
    RepoBundle,
    WorkspaceResult,
)


def test_any_dirty_skip_true_when_marker_present(tmp_path: Path, make_workspace) -> None:
    wr = WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label=""),
        status="skip",
        error=f"{models_mod.DIRTY_SKIP_MARKER} (name the checkout as a root to update it anyway)",
    )
    bundle = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr])
    assert bundle.any_dirty_skip() is True


def test_any_dirty_skip_false_when_marker_absent(tmp_path: Path, make_workspace) -> None:
    wr = WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label=""),
        status="skip",
        error="some other reason",
    )
    bundle = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr])
    assert bundle.any_dirty_skip() is False


def test_any_dirty_skip_uses_shared_constant_not_a_hardcoded_literal(
    monkeypatch,
    tmp_path: Path,
    make_workspace,
) -> None:
    """any_dirty_skip() must key off the module constant, not a duplicated
    literal -- so retargeting the constant changes detection accordingly.
    """
    monkeypatch.setattr(models_mod, "DIRTY_SKIP_MARKER", "totally-different-marker")
    wr = WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label=""),
        status="skip",
        error="dirty git worktree (name the checkout as a root to update it anyway)",
    )
    bundle = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr])
    # Monkeypatched constant; fixture uses a mismatched skip marker.
    assert bundle.any_dirty_skip() is False

    wr2 = WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label=""),
        status="skip",
        error="totally-different-marker present",
    )
    bundle2 = RepoBundle(git_repo=Path("/tmp/repo"), label="", workspaces=[wr2])
    assert bundle2.any_dirty_skip() is True


def test_workspace_result_to_json_includes_started_dirty(tmp_path: Path, make_workspace) -> None:
    wr = WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label=""),
        status="ok",
        changes=[Change(name="pkg", from_ver="1.0", to="1.1")],
        started_dirty=True,
    )
    payload = wr.to_json()
    assert payload["started_dirty"] is True
    assert payload["changes"] == [{"name": "pkg", "from": "1.0", "to": "1.1"}]
    assert (
        "started_dirty"
        not in WorkspaceResult(
            workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label="")
        ).to_json()
    )


def test_workspace_result_to_json_truncates_verify_error(tmp_path: Path, make_workspace) -> None:
    wr = WorkspaceResult(
        workspace=make_workspace(tmp_path, ecosystem="py", manager="uv", label=""),
        status="fail",
        verify_error="x" * (models_mod.VERIFY_ERROR_MAX + 40),
    )
    payload = wr.to_json()
    assert payload["verify_error"].endswith("...")
    assert len(payload["verify_error"]) == models_mod.VERIFY_ERROR_MAX
    wr.verify_error = "Authorization: secret-token-value"
    assert "secret-token-value" not in wr.to_json()["verify_error"]


def test_action_bump_to_json_optional_detail() -> None:
    bare = ActionBump(
        workflow=".github/workflows/ci.yml",
        action="actions/checkout",
        pin="v4",
        latest="v4",
    )
    assert bare.to_json() == {
        "workflow": ".github/workflows/ci.yml",
        "action": "actions/checkout",
        "pin": "v4",
        "latest": "v4",
        "status": "ok",
    }
    with_detail = ActionBump(
        workflow=".github/workflows/ci.yml",
        action="actions/checkout",
        pin="v3",
        latest="v4",
        detail="newer major available; not auto-applied",
    )
    assert with_detail.to_json()["detail"].startswith("newer major")


def test_repo_bundle_actions_summary_counts() -> None:
    bundle = RepoBundle(
        git_repo=Path("/tmp/repo"),
        label="demo",
        actions_workflows_scanned=2,
        actions=[
            ActionBump(
                workflow="a.yml",
                action="actions/checkout",
                pin="v3",
                latest="v4",
            ),
            ActionBump(
                workflow="b.yml",
                action="actions/setup-node",
                pin="v4",
                latest="v5",
                detail="newer major available; not auto-applied",
            ),
            ActionBump(
                workflow="c.yml",
                action="actions/cache",
                pin="v3",
                latest="v3",
                status="fail",
            ),
        ],
    )
    summary = bundle.actions_summary()
    assert summary["workflows_scanned"] == 2
    assert summary["bumps"] == 1
    assert summary["behind_latest"] == 1
    assert summary["failures"] == 1
    payload = bundle.to_json()
    assert payload["actions_summary"] == summary
