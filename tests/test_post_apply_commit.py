"""Post-apply commit automation (#15)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import post_apply_commit


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _bundle(tmp_path: Path, *, started_dirty: bool = False) -> RepoBundle:
    _init_git_repo(tmp_path)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.started_dirty = started_dirty
    return RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])


def test_forbidden_env_blocks_commit() -> None:
    assert post_apply_commit.is_forbidden_commit_path(".env")
    assert post_apply_commit.is_forbidden_commit_path("apps/web/.env.local")
    assert not post_apply_commit.is_forbidden_commit_path("package-lock.json")


def test_commit_dry_run_lists_files(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion":1}', encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.files
    outcome = post_apply_commit.execute_commit_plan(plan, dry_run=True)
    assert outcome.status == "dry_run"
    assert "chore(deps)" in (outcome.message or "")


def test_skips_verify_fail_by_default(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle.workspaces[0].status = "fail"
    bundle.workspaces[0].verify_error = "build failed"
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason == "verify failed"


def test_skips_dirty_at_start_unless_allowed(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, started_dirty=True)
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason == "dirty worktree at start"


def test_agent_missing_api_key_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="agent", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason
    assert "CURSOR_API_KEY" in plan.skipped_reason


def test_agent_drafts_message_when_mocked(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    fake_result = MagicMock(status="ok", result="chore(deps): bump lockfile via agent")
    fake_agent = MagicMock()
    fake_agent.prompt.return_value = fake_result
    with (
        patch.dict("os.environ", {"CURSOR_API_KEY": "cursor_test_key"}),
        patch.dict(
            "sys.modules",
            {
                "cursor_sdk": MagicMock(
                    Agent=MagicMock(prompt=fake_agent.prompt),
                    AgentOptions=MagicMock,
                    LocalAgentOptions=MagicMock,
                )
            },
        ),
        patch(
            "repo_ops_upgrade.post_apply_commit.draft_message_with_agent",
            return_value="chore(deps): bump lockfile via agent\n\nAutomated post-apply commit (Cursor SDK draft).",
        ),
    ):
        plan = post_apply_commit.plan_repo_commit(
            bundle, mode="agent", allow_dirty=False, commit_on_fail=False
        )
    assert plan.message
    assert "agent" in plan.message or "deps" in plan.message


def test_conventional_commit_writes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"v":2}', encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    outcome = post_apply_commit.execute_commit_plan(plan, dry_run=False)
    assert outcome.status == "committed"
    log = subprocess.run(
        ["git", "log", "-1", "--oneline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "chore(deps)" in log.stdout
