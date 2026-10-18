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


def _npm_ws(path: Path, label: str = "demo") -> Workspace:
    return Workspace(
        path=path,
        ecosystem="node",
        manager="npm",
        git_repo=path,
        label=label,
    )


def test_exclude_drops_matching_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    upgrade_args,
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
        args=upgrade_args(exclude_patterns=["vendor"]),
        roots=[str(tmp_path)],
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "excluded 1 repo(s) by exclude patterns" in err
    assert processed == ["org/keep-repo"]


def test_exclude_never_drops_a_named_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upgrade_args
) -> None:
    """A checkout named on the command line is processed even when a pattern matches it."""
    repo = tmp_path / "vendor-repo"
    repo.mkdir()
    ws = _npm_ws(repo, "org/vendor-repo")
    bundles = {
        repo: RepoBundle(
            git_repo=repo, label="org/vendor-repo", workspaces=[WorkspaceResult(workspace=ws)]
        )
    }
    processed: list[str] = []
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)

    def _process(wr: WorkspaceResult, **kw) -> WorkspaceResult:
        processed.append(wr.workspace.label)
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)
    run_mod._run_upgrade_body(
        apply=False, args=upgrade_args(exclude_patterns=["vendor"]), roots=[str(repo)]
    )
    assert processed == ["org/vendor-repo"]


def test_children_first_processes_child_depth_before_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upgrade_args
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
        args=upgrade_args(
            submodule_order="children-first",
            skip_actions=True,
            jobs=1,
            no_commit=True,
        ),
        roots=[str(tmp_path)],
    )
    assert order == ["child", "parent"], f"expected child then parent, got {order}"


def test_failed_repo_is_retried_alone_after_the_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo, upgrade_args
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
        args=upgrade_args(no_verify=False, no_commit=False),
        roots=[str(tmp_path)],
        fleet=True,
    )

    assert rc == 0
    assert sorted(calls) == ["org/flaky", "org/flaky", "org/steady"]
    assert sorted(committed) == ["org/flaky", "org/steady"]


def test_mock_args_without_no_verify_still_verify_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``not args.no_verify`` treats a MagicMock auto-attr as True and skips
    both verify and commit. Identity ``is True`` via ``_cli_flag`` does not."""
    repo = tmp_path / "demo"
    repo.mkdir()
    ws = _npm_ws(repo)
    bundles = {
        repo: RepoBundle(
            git_repo=repo, label="org/demo", workspaces=[WorkspaceResult(workspace=ws)]
        )
    }
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)

    seen: dict[str, bool] = {}

    def _process(wr: WorkspaceResult, **kw: object) -> WorkspaceResult:
        seen["do_verify"] = bool(kw.get("do_verify"))
        wr.status = "ok"
        wr.changes = [Change("pkg", "1", "2")]
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)
    committed: list[str] = []

    def _commits(b: dict, **kw: object) -> tuple[list[CommitOutcome], int]:
        committed.extend(bundle.label for bundle in b.values())
        return [], 0

    monkeypatch.setattr(run_mod.post_apply_commit, "run_post_apply_commits", _commits)

    args = MagicMock()
    args.jobs = 1
    args.eco_filter = None
    args.exclude_patterns = None
    args.no_commit = False
    # no_verify left unset so MagicMock fabricates a truthy attribute

    rc = run_mod._run_upgrade_body(apply=True, args=args, roots=[str(tmp_path)], fleet=False)
    assert rc == 0
    assert seen.get("do_verify") is True
    assert committed == ["org/demo"]


def test_no_verify_prints_commit_skip_unless_no_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    upgrade_args,
) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    ws = _npm_ws(repo)
    bundles = {
        repo: RepoBundle(
            git_repo=repo, label="org/demo", workspaces=[WorkspaceResult(workspace=ws)]
        )
    }
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)

    def _ok(wr: WorkspaceResult, **kw: object) -> WorkspaceResult:
        wr.status = "ok"
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _ok)
    commits: list[object] = []

    def _no_commits(*a: object, **kw: object) -> tuple[list[CommitOutcome], int]:
        commits.append(1)
        return [], 0

    monkeypatch.setattr(run_mod.post_apply_commit, "run_post_apply_commits", _no_commits)

    rc = run_mod._run_upgrade_body(
        apply=True,
        args=upgrade_args(no_verify=True, no_commit=False),
        roots=[str(tmp_path)],
        fleet=False,
    )
    assert rc == 0
    assert commits == []
    note = "[commit skip]: verify was skipped so commit was skipped"
    captured = capsys.readouterr()
    assert captured.out.count(note) == 1

    run_mod._run_upgrade_body(
        apply=True,
        args=upgrade_args(no_verify=True, no_commit=True),
        roots=[str(tmp_path)],
        fleet=False,
    )
    both = capsys.readouterr()
    assert note not in both.out
    assert note not in both.err


def test_manager_filter_keeps_only_matching_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upgrade_args
) -> None:
    uv_path = tmp_path / "uv-ws"
    poetry_path = tmp_path / "poetry-ws"
    uv_path.mkdir()
    poetry_path.mkdir()
    ws_uv = Workspace(path=uv_path, ecosystem="py", manager="uv", git_repo=uv_path, label="uv-ws")
    ws_poetry = Workspace(
        path=poetry_path,
        ecosystem="py",
        manager="poetry",
        git_repo=poetry_path,
        label="poetry-ws",
    )
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws_uv, ws_poetry])
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    processed: list[str] = []

    def _process(wr: WorkspaceResult, **kw: object) -> WorkspaceResult:
        processed.append(wr.workspace.manager)
        wr.status = "ok"
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)
    rc = run_mod._run_upgrade_body(
        apply=False,
        args=upgrade_args(manager_filter="uv"),
        roots=[str(tmp_path)],
        fleet=False,
    )
    assert rc == 0
    assert processed == ["uv"]
