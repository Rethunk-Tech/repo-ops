"""Post-apply commit automation (#15)."""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import commit_agent_providers as providers
from repo_ops_upgrade import post_apply_commit
from repo_ops_upgrade.post_apply import collect_changes_after_apply


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


def test_parse_porcelain_quoted_path_and_rename() -> None:
    # Regression #72: git wraps paths containing spaces (or non-ASCII) in
    # double-quotes with C-style escapes. The quotes must be stripped and octal
    # sequences decoded before the path reaches `git add`.
    # Also verifies that a rename entry returns only the new (right-hand) path,
    # and that the new path itself may also be quoted.
    out = ' M "path with spaces.txt"\nR  "old dir/a.txt" -> "new dir/b.txt"\n'
    assert post_apply_commit._parse_porcelain(out) == [
        "path with spaces.txt",
        "new dir/b.txt",
    ]


def test_unstaged_go_change_classifies_as_go_modules() -> None:
    # End-to-end of the bug: a real unstaged go.mod/go.sum change must yield
    # the Go subject, not the generic fallback.
    paths = post_apply_commit._parse_porcelain(" M go.mod\n M go.sum\n")
    assert post_apply_commit.classify_commit_subject(paths) == (
        "chore(deps): update Go modules"
    )


def test_classify_bun_lock_text_format_is_node() -> None:
    """Issue #80: bun.lock (Bun >=1.0 text format) must classify as Node, not generic."""
    assert post_apply_commit.classify_commit_subject(["bun.lock"]) == (
        "chore(deps): update Node lockfiles and manifests"
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


def test_agent_non_runtime_error_skips_instead_of_propagating(
    tmp_path: Path,
) -> None:
    """Regression #84: non-RuntimeError from Agent.prompt must not crash plan_repo_commit.

    A network/transport error (ConnectionError, TimeoutError, etc.) raised inside
    draft_message_with_commit_agent must degrade to a skipped-commit result with a
    reason, not propagate out of plan_repo_commit and crash the commit phase.
    """
    bundle = _bundle(tmp_path)
    lock = tmp_path / "package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    with patch(
        "repo_ops_upgrade.commit_agent_providers.draft_message_with_commit_agent",
        side_effect=ConnectionError("network down"),
    ):
        plan = post_apply_commit.plan_repo_commit(
            bundle, mode="agent", allow_dirty=False, commit_on_fail=False
        )
    assert plan.skipped_reason == "network down"
    assert plan.message == ""


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

    def _block_cursor_sdk(
        name: str,
        _globals: Mapping[str, object] | None = None,
        _locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> types.ModuleType:
        if name == "cursor_sdk" or name.startswith("cursor_sdk."):
            raise ImportError("cursor-sdk not installed")
        return real_import(name, _globals, _locals, fromlist, level)

    sys.modules.pop("cursor_sdk", None)
    with (
        patch("builtins.__import__", side_effect=_block_cursor_sdk),
        pytest.raises(RuntimeError, match="commit-agent-cursor"),
    ):
        providers.resolve_commit_agent_provider().draft_commit_message(
            bundle, ["package-lock.json"]
        )


def test_collect_changes_no_phantom_downgrade(tmp_path: Path) -> None:
    """Regression #63: removed (-) package.json lines must not produce phantom Changes."""
    # Use a subdirectory workspace so rel_s ("app") is a real prefix of the
    # path returned by git diff --name-only ("app/package.json").
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    ws = Workspace(
        path=app_dir,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    # Simulate a git diff where pkg bumps from 1.0.0 to 1.1.0.
    # The [+-] regex bug would yield two Changes; the fixed \+ regex yields one.
    pkg_diff = (
        "diff --git a/app/package.json b/app/package.json\n"
        "--- a/app/package.json\n"
        "+++ b/app/package.json\n"
        "@@ -1,5 +1,5 @@\n"
        " {\n"
        '   "dependencies": {\n'
        '-    "pkg": "1.0.0"\n'
        '+    "pkg": "1.1.0"\n'
        "   }\n"
        " }\n"
    )

    def _fake_run(
        cmd: list[str], cwd: object, **kwargs: object
    ) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            return CompletedProcess(cmd, 0, stdout="app/package.json\n", stderr="")
        # git diff -- app/package.json
        return CompletedProcess(cmd, 0, stdout=pkg_diff, stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    assert len(changes) == 1, f"expected 1 Change, got {changes}"
    assert changes[0].name == "pkg"
    assert changes[0].to == "1.1.0"


def test_collect_changes_root_workspace_and_non_dep_fields_excluded(
    tmp_path: Path,
) -> None:
    """Issues #82 + #83: root workspace (rel_s==".") must not drop diff paths,
    and non-dependency fields (version, name, scripts) must not appear as Changes."""
    # Root workspace: path == git_repo so relative_to yields "."
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="root",
    )
    # Diff contains a top-level "version" field change AND a real dep bump.
    # Only the dep inside "dependencies" section must be returned.
    pkg_diff = (
        "diff --git a/package.json b/package.json\n"
        "--- a/package.json\n"
        "+++ b/package.json\n"
        "@@ -1,9 +1,9 @@\n"
        " {\n"
        '-  "version": "1.0.0",\n'
        '+  "version": "1.1.0",\n'
        '   "dependencies": {\n'
        '-    "react": "18.0.0"\n'
        '+    "react": "18.3.0"\n'
        "   }\n"
        " }\n"
    )

    def _fake_run(
        cmd: list[str], cwd: object, **kwargs: object
    ) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            # bare "package.json" — no leading subdirectory prefix
            return CompletedProcess(cmd, 0, stdout="package.json\n", stderr="")
        return CompletedProcess(cmd, 0, stdout=pkg_diff, stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    names = {c.name for c in changes}
    # "react" dep bump captured (#83 root-workspace fix makes this reachable)
    assert "react" in names, f"expected react dep in changes, got {changes}"
    # "version" top-level field must not appear (#82 scoping fix)
    assert "version" not in names, f"version field leaked into changes: {changes}"


def test_collect_changes_json_yaml_lockb_fallback(tmp_path: Path) -> None:
    """Regression #89: package-lock.json, pnpm-lock.yaml, bun.lockb must appear in fallback changes.

    These filenames don't match .endswith(".lock") but are in NODE_LOCKFILES, so
    the git diff --name-only gate already filters to them. The elif must use
    set(lock_names) membership rather than the .endswith heuristic.
    """
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="root",
    )

    def _fake_run(
        cmd: list[str], cwd: object, **kwargs: object
    ) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            return CompletedProcess(
                cmd,
                0,
                stdout="package-lock.json\npnpm-lock.yaml\nbun.lockb\n",
                stderr="",
            )
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    names = {c.name for c in changes}
    assert "package-lock.json" in names, (
        f"package-lock.json missing from changes: {changes}"
    )
    assert "pnpm-lock.yaml" in names, f"pnpm-lock.yaml missing from changes: {changes}"
    assert "bun.lockb" in names, f"bun.lockb missing from changes: {changes}"
