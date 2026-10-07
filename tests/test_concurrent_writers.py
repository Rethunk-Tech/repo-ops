"""A failed-verify rollback never touches what another process wrote."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops._register import register_all
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import discovery as disc_mod
from repo_ops_upgrade import orchestration
from repo_ops_upgrade import orchestration as orch_mod
from repo_ops_upgrade import run as run_mod
from repo_ops_upgrade.post_apply import FAILED_WITH_CHANGES
from repo_ops_upgrade.verify import verify_node


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path, init_git_repo) -> Workspace:
    init_git_repo(tmp_path)
    for name, text in {
        "package.json": "{}\n",
        "package-lock.json": "{}\n",
        "README.md": "v1\n",
    }.items():
        (tmp_path / name).write_text(text)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label=".")


def _apply_and_fail_verify(ws: Workspace, during_verify) -> WorkspaceResult:
    root = ws.path

    def _apply(_ws: Workspace) -> None:
        (root / "package.json").write_text('{"dependencies":{"a":"2"}}\n')
        (root / "package-lock.json").write_text('{"a":"2"}\n')

    def _verify(_ws: Workspace, **_kw) -> str:
        during_verify(root)
        return "gate failed"

    register_all()
    with (
        patch("repo_ops_upgrade.orchestration.ecosystems.apply_workspace", side_effect=_apply),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations"),
        patch("repo_ops_upgrade.orchestration.verify_mod.verify_workspace", side_effect=_verify),
    ):
        return orchestration.process_workspace(
            WorkspaceResult(workspace=ws),
            do_apply=True,
            do_verify=True,
            allow_dirty=False,
            on_verify_fail="revert",
        )


def test_concurrent_edits_survive_a_failed_verify_rollback(tmp_path: Path, init_git_repo) -> None:
    ws = _repo(tmp_path, init_git_repo)

    def _agent(root: Path) -> None:
        (root / "README.md").write_text("agent edit\n")
        (root / "new_file.txt").write_text("agent file\n")

    out = _apply_and_fail_verify(ws, _agent)

    assert (tmp_path / "README.md").read_text() == "agent edit\n"
    assert (tmp_path / "new_file.txt").read_text() == "agent file\n"
    assert (tmp_path / "package.json").read_text() == "{}\n"
    assert (tmp_path / "package-lock.json").read_text() == "{}\n"
    assert out.status == "fail"
    assert out.reverted_ok is False
    assert out.verify_fail_disposition == FAILED_WITH_CHANGES
    assert "README.md" in (out.error or "")
    assert "new_file.txt" in (out.error or "")


def test_an_owned_file_changed_by_someone_else_is_left_alone(tmp_path: Path, init_git_repo) -> None:
    ws = _repo(tmp_path, init_git_repo)

    def _agent(root: Path) -> None:
        (root / "package.json").write_text('{"dependencies":{"a":"2"},"scripts":{"x":"y"}}\n')

    out = _apply_and_fail_verify(ws, _agent)

    assert "scripts" in (tmp_path / "package.json").read_text()
    assert (tmp_path / "package-lock.json").read_text() == "{}\n"
    assert out.verify_fail_disposition == FAILED_WITH_CHANGES
    assert "package.json" in (out.error or "")


def _two_repo_bundles(tmp_path: Path, init_git_repo):
    repo = tmp_path / "flaky"
    repo.mkdir()
    init_git_repo(repo)
    ws = Workspace(path=repo, ecosystem="go", manager="go", git_repo=repo, label="org/flaky")
    return (
        repo,
        ws,
        {
            repo: RepoBundle(
                git_repo=repo, label="org/flaky", workspaces=[WorkspaceResult(workspace=ws)]
            )
        },
    )


def test_retry_is_skipped_when_the_checkout_became_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo, upgrade_args
) -> None:
    repo, ws, bundles = _two_repo_bundles(tmp_path, init_git_repo)
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [ws])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(run_mod, "reinstall_from_lockfile", lambda *a, **kw: True)
    monkeypatch.setattr(
        run_mod.post_apply_commit, "run_post_apply_commits", lambda b, **kw: ([], 0)
    )
    calls: list[str] = []

    def _process(wr: WorkspaceResult, **kw) -> WorkspaceResult:
        calls.append(wr.workspace.label)
        wr.status = "fail"
        wr.error = "gate failed"
        (repo / "agent.txt").write_text("someone else's work\n")
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)

    rc = run_mod._run_upgrade_body(
        apply=True,
        args=upgrade_args(no_verify=False, no_commit=False),
        roots=[str(tmp_path)],
        fleet=True,
    )

    assert rc == 1
    assert calls == ["org/flaky"]
    assert (repo / "agent.txt").exists()
    assert "not retried" in (bundles[repo].workspaces[0].error or "")


def test_a_sibling_made_dirty_mid_bundle_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo, upgrade_args
) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    init_git_repo(repo)
    wss = [
        Workspace(path=repo, ecosystem="go", manager="go", git_repo=repo, label="r/a"),
        Workspace(path=repo, ecosystem="node", manager="npm", git_repo=repo, label="r/b"),
    ]
    bundles = {
        repo: RepoBundle(
            git_repo=repo, label="r", workspaces=[WorkspaceResult(workspace=w) for w in wss]
        )
    }
    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: wss)
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        run_mod.post_apply_commit, "run_post_apply_commits", lambda b, **kw: ([], 0)
    )
    started: list[tuple[str, bool | None]] = []

    def _process(wr: WorkspaceResult, **kw) -> WorkspaceResult:
        started.append((wr.workspace.label, kw["repo_started_dirty"]))
        if wr.workspace.label == "r/a":
            (repo / "go.mod").write_text("applied by r/a\n")
        return wr

    monkeypatch.setattr(orch_mod, "process_workspace", _process)
    real_capture = run_mod.capture_worktree_state
    captures: list[int] = []

    def _capture(path: Path):
        state = real_capture(path)
        captures.append(1)
        if len(captures) == 2:  # right after r/a: its own go.mod is expected, this is not
            (repo / "agent.txt").write_text("someone else\n")
        return state

    monkeypatch.setattr(run_mod, "capture_worktree_state", _capture)
    run_mod._run_upgrade_body(
        apply=True,
        args=upgrade_args(no_verify=False, no_commit=False),
        roots=[str(tmp_path)],
        fleet=True,
    )

    assert started == [("r/a", False), ("r/b", True)]


def test_verify_runs_only_gates_a_default_gate_run_runs(tmp_path: Path, make_workspace) -> None:
    (tmp_path / "package.json").write_text("{}")
    listing = json.dumps(
        {
            "gates": [
                {"name": "build"},
                {"name": "gate"},
                {"name": "e2e", "e2e": True, "skipped": True},
                {"name": "perf", "e2e": True, "skipped": True},
            ]
        }
    )
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None, env=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=listing if "--json" in cmd else "", stderr=""
        )

    ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
    with (
        patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
        patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
    ):
        assert verify_node(ws) is None

    assert calls[-1] == ["gate", "--timeout", "0", "run", "build", "gate"]
