"""Post-apply commit automation (#15)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import commit_agent_providers as providers
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


def test_parse_porcelain_unstaged_preserves_filename() -> None:
    # Regression: unstaged entries (" M path") have a space in the X column.
    # Stripping the line before slicing dropped the filename's first char
    # ("go.mod" -> "o.mod"), which broke staging and subject classification.
    out = " M go.mod\n M go.sum\n"
    assert post_apply_commit._parse_porcelain(out) == ["go.mod", "go.sum"]


def test_parse_porcelain_staged_and_rename() -> None:
    out = "M  package-lock.json\nR  old.txt -> new.txt\n?? extra.json\n"
    assert post_apply_commit._parse_porcelain(out) == [
        "package-lock.json",
        "new.txt",
        "extra.json",
    ]


def test_unstaged_go_change_classifies_as_go_modules() -> None:
    # End-to-end of the bug: a real unstaged go.mod/go.sum change must yield
    # the Go subject, not the generic fallback.
    paths = post_apply_commit._parse_porcelain(" M go.mod\n M go.sum\n")
    assert post_apply_commit.classify_commit_subject(paths) == (
        "chore(deps): update Go modules"
    )


def test_skips_verify_fail_by_default(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle.workspaces[0].status = "fail"
    bundle.workspaces[0].verify_error = "build failed"
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason == "verify failed"


def test_allow_dirty_scopes_commit_to_upgrade_paths(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, started_dirty=True)
    (tmp_path / "package-lock.json").write_text('{"v":2}', encoding="utf-8")
    (tmp_path / "wip.txt").write_text("unrelated", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=True, commit_on_fail=False
    )
    assert plan.files == ["package-lock.json"]
    assert "wip.txt" not in plan.files


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
    assert "commit-agent-cursor" in plan.skipped_reason


def test_agent_drafts_message_when_mocked(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    with patch(
        "repo_ops_upgrade.commit_agent_providers.draft_message_with_commit_agent",
        return_value=(
            "chore(deps): bump lockfile via agent\n\n"
            "Automated post-apply commit (cursor commit-agent)."
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


def test_post_apply_commit_import_does_not_load_cursor_sdk() -> None:
    sys.modules.pop("cursor_sdk", None)
    import importlib

    importlib.reload(post_apply_commit)
    assert "cursor_sdk" not in sys.modules


def test_unknown_commit_agent_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(providers.COMMIT_AGENT_PROVIDER_ENV, "not-a-provider")
    with pytest.raises(RuntimeError, match="Unknown commit agent provider"):
        providers.resolve_commit_agent_provider()


def test_default_provider_is_cursor() -> None:
    providers._ensure_builtin_providers()
    assert providers.commit_agent_provider_name() == "cursor"
    provider = providers.resolve_commit_agent_provider()
    assert provider.name == "cursor"


def test_cursor_provider_missing_sdk_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(tmp_path)
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real_import = __import__

    def _block_cursor_sdk(name: str, *args: object, **kwargs: object) -> object:
        if name == "cursor_sdk" or name.startswith("cursor_sdk."):
            raise ImportError("cursor-sdk not installed")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("cursor_sdk", None)
    with patch("builtins.__import__", side_effect=_block_cursor_sdk):
        with pytest.raises(RuntimeError, match="commit-agent-cursor"):
            providers.resolve_commit_agent_provider().draft_commit_message(
                bundle, ["package-lock.json"]
            )
