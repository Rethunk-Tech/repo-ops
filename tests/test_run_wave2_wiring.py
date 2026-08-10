"""Run-path wiring tests for W7 findings (exclude, skip-remediations, PR, levels)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import discovery as disc_mod
from repo_ops_upgrade import orchestration as orch_mod
from repo_ops_upgrade import run as run_mod
from repo_ops_upgrade import submodules as sub_mod
from repo_ops_upgrade.post_apply_commit import CommitOutcome
from repo_ops_upgrade.pr import PROutcome


def _fake_args(**overrides) -> MagicMock:
    defaults: dict = {
        "skip_actions": True,
        "flat": True,
        "eco_filter": None,
        "manager_filter": None,
        "exclude_patterns": None,
        "submodules": False,
        "submodule_order": "discovery",
        "latest": False,
        "full": False,
        "yarn_targeted_bump": False,
        "npm_targeted_bump": False,
        "pnpm_targeted_bump": False,
        "bun_targeted_bump": False,
        "deno_targeted_bump": False,
        "migrate_to_uv_lock": False,
        "yarn_jobs": None,
        "commit_umbrella": False,
        "actions_org_policy": False,
        "actions_policy_level": None,
        "go_upgrade_mode": None,
        "actions_unpin_sha": False,
        "actions_refresh_sha": False,
        "precommit_refresh_sha": False,
        "submodule_refresh_sha": False,
        "actions_tag_style": "major",
        "actions_upgrade_mode": None,
        "actions_lint_level": None,
        "allow_dirty": True,
        "strict_exit": False,
        "json": False,
        "json_report_path": None,
        "compact": False,
        "quiet": True,
        "top": 10,
        "max_failures": 20,
        "only_outdated": False,
        "fail_fast": False,
        "jobs": 1,
        "skip_remediations": "",
        "no_verify": True,
        "verify_test": False,
        "on_verify_fail": "keep",
        "commit": "none",
        "commit_dry_run": False,
        "commit_on_fail": False,
        "pr": False,
        "pr_base": None,
        "branch_prefix": "repo-ops/deps",
        "resume": False,
        "follow_symlinks": False,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_full_sweep_sets_policy_env_and_refreshes_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--full composes latest, digest refresh, and targeted bump policy."""
    env_names = [
        "REPO_OPS_BUN_UPDATE_LATEST",
        "REPO_OPS_PNPM_UPDATE_LATEST",
        "REPO_OPS_YARN_UPDATE_LATEST",
        "REPO_OPS_DENO_UPDATE_LATEST",
        "REPO_OPS_CONTAINER_UPDATE_LATEST",
        "REPO_OPS_PRECOMMIT_UPDATE_LATEST",
        "REPO_OPS_SUBMODULE_UPDATE_LATEST",
        "REPO_OPS_GO_UPGRADE_MODE",
        "REPO_OPS_CONTAINER_REFRESH_SHA",
        "REPO_OPS_PRECOMMIT_REFRESH_SHA",
        "REPO_OPS_SUBMODULE_REFRESH_SHA",
        "REPO_OPS_YARN_TARGETED_BUMP",
        "REPO_OPS_NPM_TARGETED_BUMP",
        "REPO_OPS_PNPM_TARGETED_BUMP",
        "REPO_OPS_BUN_TARGETED_BUMP",
        "REPO_OPS_DENO_TARGETED_BUMP",
    ]
    for name in env_names:
        monkeypatch.delenv(name, raising=False)

    ws = _npm_ws(tmp_path)
    wr = WorkspaceResult(workspace=ws)
    bundles = {tmp_path: RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])}
    action_kwargs: list[dict] = []

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        orch_mod,
        "process_workspace",
        lambda result, **kw: result,
    )
    monkeypatch.setattr(
        run_mod.actions,
        "scan_actions",
        lambda *a, **kw: action_kwargs.append(kw) or ([], 0),
    )

    rc = run_mod._run_upgrade_body(
        apply=False,
        args=_fake_args(
            full=True,
            skip_actions=False,
            go_upgrade_mode="patch",
            actions_upgrade_mode="normalize",
        ),
        roots=[str(tmp_path)],
    )

    assert rc == 0
    for name in env_names:
        assert os.environ[name] == (
            "patch" if name == "REPO_OPS_GO_UPGRADE_MODE" else "1"
        )
    assert action_kwargs
    assert action_kwargs[0]["refresh_sha"] is True
    assert action_kwargs[0]["upgrade_mode"] == "normalize"


def _npm_ws(path: Path, label: str = "demo") -> Workspace:
    return Workspace(
        path=path,
        ecosystem="node",
        manager="npm",
        git_repo=path,
        label=label,
    )


def test_exclude_drops_matching_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--exclude removes matching bundles and prints footer count (F140)."""
    keep = tmp_path / "keep-repo"
    drop = tmp_path / "vendor-repo"
    keep.mkdir()
    drop.mkdir()
    ws_keep = _npm_ws(keep, "org/keep-repo")
    ws_drop = _npm_ws(drop, "org/vendor-repo")
    bundles = {
        keep: RepoBundle(
            git_repo=keep,
            label="org/keep-repo",
            workspaces=[WorkspaceResult(workspace=ws_keep)],
        ),
        drop: RepoBundle(
            git_repo=drop,
            label="org/vendor-repo",
            workspaces=[WorkspaceResult(workspace=ws_drop)],
        ),
    }
    processed: list[str] = []

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws_keep, ws_drop])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)

    def _process(wr: WorkspaceResult, **kw) -> WorkspaceResult:
        processed.append(wr.workspace.label)
        wr.status = "ok"
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)

    rc = run_mod._run_upgrade_body(
        apply=False,
        args=_fake_args(exclude_patterns=["vendor"], quiet=False),
        roots=[str(tmp_path)],
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "excluded 1 repo(s) by exclude patterns" in err
    assert processed == ["org/keep-repo"]


def test_skip_remediations_passed_to_process_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--skip-remediations CSV is forwarded into process_workspace (F044)."""
    ws = _npm_ws(tmp_path)
    wr = WorkspaceResult(workspace=ws)
    bundles = {tmp_path: RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])}
    captured: list[set[str]] = []

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)

    def _process(result: WorkspaceResult, **kw) -> WorkspaceResult:
        captured.append(set(kw.get("skip_remediations") or set()))
        result.status = "ok"
        return result

    monkeypatch.setattr(orch_mod, "process_workspace", _process)

    run_mod._run_upgrade_body(
        apply=True,
        args=_fake_args(skip_remediations="go_generate,biome_schema"),
        roots=[str(tmp_path)],
    )
    assert captured
    assert captured[0] == {"go_generate", "biome_schema"}


def test_children_first_processes_child_depth_before_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """children-first level scheduling waits for child depth (F143)."""
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    child.mkdir()
    ws_p = _npm_ws(parent, "parent")
    ws_c = _npm_ws(child, "child")
    bundles = {
        parent: RepoBundle(
            git_repo=parent,
            label="parent",
            workspaces=[WorkspaceResult(workspace=ws_p)],
        ),
        child: RepoBundle(
            git_repo=child,
            label="child",
            workspaces=[WorkspaceResult(workspace=ws_c)],
        ),
    }
    order: list[str] = []

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws_p, ws_c])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        sub_mod,
        "discover_submodules_under_roots",
        lambda *a, **kw: [
            sub_mod.SubmoduleLink(path="child", git_repo=child, parent=parent, url="x")
        ],
    )

    def _process(wr: WorkspaceResult, **kw) -> WorkspaceResult:
        order.append(wr.workspace.label)
        wr.status = "ok"
        wr.changes = [Change("x", "1", "2")]
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)

    run_mod._run_upgrade_body(
        apply=True,
        args=_fake_args(
            submodule_order="children-first",
            skip_actions=True,
            jobs=1,
            commit="none",
        ),
        roots=[str(tmp_path)],
    )
    assert order == ["child", "parent"], f"expected child then parent, got {order}"


def test_pr_dry_run_skips_run_pr_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--pr with --commit-dry-run prints skip and does not call run_pr_flow (F141)."""
    ws = _npm_ws(tmp_path)
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    bundles = {tmp_path: RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])}
    pr_calls: list = []

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        orch_mod,
        "process_workspace",
        lambda result, **kw: result,
    )
    monkeypatch.setattr(
        run_mod.post_apply_commit,
        "run_post_apply_commits",
        lambda *a, **kw: (
            [
                CommitOutcome(
                    git_repo=tmp_path,
                    label="demo",
                    status="dry_run",
                    message="chore(deps)",
                    files=["package-lock.json"],
                )
            ],
            0,
        ),
    )

    with patch("repo_ops_upgrade.pr.run_pr_flow") as mock_pr:
        mock_pr.side_effect = lambda *a, **kw: (
            pr_calls.append(1) or PROutcome(tmp_path, "demo", "opened")
        )
        rc = run_mod._run_upgrade_body(
            apply=True,
            args=_fake_args(
                pr=True,
                commit="conventional",
                commit_dry_run=True,
                quiet=False,
                skip_actions=True,
            ),
            roots=[str(tmp_path)],
        )

    assert rc == 0
    assert pr_calls == []
    assert "dry-run: branch/PR creation skipped" in capsys.readouterr().err


def test_pr_failed_outcome_sets_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_pr_flow status=failed propagates to exit_code (F141)."""
    ws = _npm_ws(tmp_path)
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    bundles = {tmp_path: RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])}

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        orch_mod,
        "process_workspace",
        lambda result, **kw: result,
    )
    monkeypatch.setattr(
        run_mod.post_apply_commit,
        "run_post_apply_commits",
        lambda *a, **kw: (
            [
                CommitOutcome(
                    git_repo=tmp_path,
                    label="demo",
                    status="committed",
                    message="chore(deps)",
                    files=["package-lock.json"],
                )
            ],
            0,
        ),
    )

    with (
        patch(
            "repo_ops_upgrade.pr.run_pr_flow",
            return_value=PROutcome(tmp_path, "demo", "failed", detail="auth"),
        ),
        patch("repo_ops_upgrade.pr.print_pr_outcome"),
    ):
        rc = run_mod._run_upgrade_body(
            apply=True,
            args=_fake_args(
                pr=True,
                commit="conventional",
                commit_dry_run=False,
                quiet=True,
                skip_actions=True,
            ),
            roots=[str(tmp_path)],
        )

    assert rc == 1
