"""Run-path wiring tests for exclude, skip-remediations, PR, and level scheduling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import discovery as disc_mod
from repo_ops_upgrade import orchestration as orch_mod
from repo_ops_upgrade import run as run_mod
from repo_ops_upgrade import submodules as sub_mod
from repo_ops_upgrade.post_apply_commit import CommitOutcome


def _fake_args(**overrides) -> MagicMock:
    defaults: dict = {
        "skip_actions": True,
        "flat": True,
        "eco_filter": None,
        "manager_filter": None,
        "exclude_patterns": None,
        "submodules": False,
        "submodule_order": "discovery",
        "conservative": False,
        "migrate_to_uv_lock": False,
        "actions_org_policy": False,
        "actions_policy_level": None,
        "go_upgrade_mode": None,
        "actions_unpin_sha": False,
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
        "no_commit": True,
        "follow_symlinks": False,
        # Real run_upgrade() always sets these before _run_upgrade_body runs;
        # a bare MagicMock auto-vivifies a missing attribute into a truthy
        # non-dict mock, which corrupts settings resolution (e.g. `in` on it
        # is always True) rather than falling through to the {} default.
        "_repo_ops_global_config": {},
        "_repo_ops_ambient_env": {},
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


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
    """--exclude removes matching bundles and prints footer count."""
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
    """--skip-remediations CSV is forwarded into process_workspace."""
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
    """Children-first level scheduling waits for child depth."""
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
            no_commit=True,
        ),
        roots=[str(tmp_path)],
    )
    assert order == ["child", "parent"], f"expected child then parent, got {order}"


def test_failed_repo_is_retried_alone_after_the_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    """A repo that fails verify during the sweep gets one fresh, serial re-apply;
    a passing retry is what the report and commit phase see."""
    flaky, steady = tmp_path / "flaky", tmp_path / "steady"
    for repo in (flaky, steady):
        repo.mkdir()
        init_git_repo(repo)
    ws_flaky = Workspace(
        path=flaky, ecosystem="go", manager="go", git_repo=flaky, label="org/flaky"
    )
    ws_steady = Workspace(
        path=steady, ecosystem="go", manager="go", git_repo=steady, label="org/steady"
    )
    bundles = {
        flaky: RepoBundle(
            git_repo=flaky, label="org/flaky", workspaces=[WorkspaceResult(workspace=ws_flaky)]
        ),
        steady: RepoBundle(
            git_repo=steady, label="org/steady", workspaces=[WorkspaceResult(workspace=ws_steady)]
        ),
    }
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws_flaky, ws_steady])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(run_mod, "capture_worktree_state", lambda *a, **kw: {})
    monkeypatch.setattr(run_mod, "restore_worktree_state", lambda *a, **kw: True)
    monkeypatch.setattr(run_mod, "reinstall_from_lockfile", lambda *a, **kw: True)

    calls: list[str] = []

    def _process(wr: WorkspaceResult, **kw) -> WorkspaceResult:
        calls.append(wr.workspace.label)
        assert wr.error is None, "retry must start from a fresh result"
        if wr.workspace.label == "org/flaky" and calls.count("org/flaky") == 1:
            wr.status = "fail"
            wr.error = "gate: FAIL exit 1  govulncheck ./..."
            wr.verify_fail_disposition = "FAILED_CLEAN"
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)
    committed: list[str] = []

    def _commits(b: dict, **kw) -> tuple[list[CommitOutcome], int]:
        committed.extend(
            bundle.label
            for bundle in b.values()
            if all(w.status == "ok" for w in bundle.workspaces)
        )
        return [], 0

    monkeypatch.setattr(run_mod.post_apply_commit, "run_post_apply_commits", _commits)

    rc = run_mod._run_upgrade_body(
        apply=True,
        args=_fake_args(
            no_verify=False, on_verify_fail="revert", no_commit=False, allow_dirty=False
        ),
        roots=[str(tmp_path)],
    )

    assert rc == 0
    assert sorted(calls) == ["org/flaky", "org/flaky", "org/steady"]
    assert sorted(committed) == ["org/flaky", "org/steady"]
