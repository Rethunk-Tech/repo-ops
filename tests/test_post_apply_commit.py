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


def test_classify_pnpm_workspace_catalog_is_node() -> None:
    """pnpm workspace catalog changes must classify as Node dependencies."""
    assert post_apply_commit.classify_commit_subject(["pnpm-workspace.yaml"]) == (
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
    assert plan.skipped_reason == "verify failed: build failed"


def test_commit_on_fail_true_proceeds_after_verify_fail(tmp_path: Path) -> None:
    """--commit-on-fail allows commits when verify_error is set (F041)."""
    bundle = _bundle(tmp_path)
    bundle.workspaces[0].status = "fail"
    bundle.workspaces[0].verify_error = "build failed"
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    (tmp_path / "package-lock.json").write_text('{"v":1}', encoding="utf-8")
    subprocess.run(["git", "add", "package-lock.json"], cwd=tmp_path, check=True)
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=True
    )
    assert plan.skipped_reason is None
    assert "package-lock.json" in plan.files


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


def test_pnpm_root_workspace_yaml_in_commit_scope() -> None:
    assert post_apply_commit._manifest_names_for_manager("pnpm") == {
        "package.json",
        "pnpm-workspace.yaml",
    }


def test_dirty_start_no_upgrade_changes_skips(tmp_path: Path) -> None:
    # #108: when the repo started dirty but no workspace produced changes, the
    # commit is scoped to upgrade paths (none here), so it skips rather than
    # sweeping the stray dirty file into a commit.
    bundle = _bundle(tmp_path, started_dirty=True)
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason == "no upgrade-scoped changes to commit (dirty worktree)"


def test_dirty_sibling_does_not_block_clean_workspace_commit(tmp_path: Path) -> None:
    # #108: a dirty sibling file must not block committing a clean workspace's
    # applied bump. The root workspace's manifest AND lockfile are both scoped
    # (the manifest fix prevents a partial commit); the unrelated dirty file is
    # never staged. Fails without the root-manifest scoping fix.
    bundle = _bundle(tmp_path, started_dirty=True)
    (tmp_path / "package.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"v":2}', encoding="utf-8")
    (tmp_path / "WIP.md").write_text("unrelated pre-existing edit", encoding="utf-8")
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert set(plan.files) == {"package.json", "package-lock.json"}
    assert "WIP.md" not in plan.files


def test_skipped_dirty_sibling_workspace_does_not_block_commit(tmp_path: Path) -> None:
    # #108 (real repro): a *sibling workspace* skipped as dirty (status="skip",
    # error "dirty git worktree") must not veto committing a clean workspace's
    # applied bump. Exercises the second gate (_bundle_apply_ok), which the
    # original fix left in place — fails before the skip-dirty veto was removed.
    bundle = _bundle(tmp_path)  # clean root workspace, status "ok"
    bundle.workspaces[0].changes = [Change("pkg", "1", "2")]
    (tmp_path / "package.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"v":2}', encoding="utf-8")
    # A go sibling skipped because the repo started dirty (orchestration sets this).
    sibling = WorkspaceResult(
        workspace=Workspace(
            path=tmp_path / "cmd" / "example",
            ecosystem="go",
            manager="go",
            git_repo=tmp_path,
            label="example",
        ),
        status="skip",
    )
    sibling.error = "dirty git worktree (use --allow-dirty)"
    sibling.started_dirty = True
    bundle.workspaces.append(sibling)

    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason is None
    assert set(plan.files) == {"package.json", "package-lock.json"}


def test_actions_only_repo_commits(tmp_path: Path) -> None:
    # #107: a repo with workflow bumps but no package workspaces must still
    # commit; _bundle_apply_ok previously bailed with "no workspaces".
    from repo_ops_models import ActionBump

    _init_git_repo(tmp_path)
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text("on: push\njobs: {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    # The action bump rewrites the (tracked) workflow file.
    wf.write_text("on: push  # bumped\njobs: {}\n", encoding="utf-8")
    bundle = RepoBundle(
        git_repo=tmp_path,
        label="actions-only",
        workspaces=[],
        actions=[
            ActionBump(
                workflow=".github/workflows/ci.yml",
                action="actions/checkout",
                pin="v4",
                latest="v6",
            )
        ],
    )
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason is None
    assert ".github/workflows/ci.yml" in plan.files
    assert "chore(ci)" in plan.message


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


def test_commit_agent_provider_uses_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(providers.COMMIT_AGENT_PROVIDER_ENV, raising=False)
    xdg_config = tmp_path / "xdg"
    config_dir = xdg_config / "repo-ops"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        "commit_agent_provider = 'stub-global'\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    class _StubProvider:
        name = "stub-global"

        def draft_commit_message(self, bundle: RepoBundle, files: list[str]) -> str:
            return "chore(deps): stub"

    providers.register_commit_agent_provider("stub-global", _StubProvider)
    try:
        assert providers.commit_agent_provider_name() == "stub-global"
        assert providers.resolve_commit_agent_provider().name == "stub-global"
    finally:
        providers._FACTORIES.pop("stub-global", None)


def test_commit_agent_provider_env_overrides_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_config = tmp_path / "xdg"
    config_dir = xdg_config / "repo-ops"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        "commit_agent_provider = 'not-a-provider'\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv(providers.COMMIT_AGENT_PROVIDER_ENV, "cursor")

    assert providers.commit_agent_provider_name() == "cursor"
    assert providers.resolve_commit_agent_provider().name == "cursor"


def test_register_commit_agent_provider_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Third-party providers register + resolve (F206)."""

    class _StubProvider:
        name = "stub-wave2"

        def draft_commit_message(self, bundle: RepoBundle, files: list[str]) -> str:
            return "chore(deps): stub"

    providers._ensure_builtin_providers()
    providers.register_commit_agent_provider("stub-wave2", _StubProvider)
    try:
        monkeypatch.setenv(providers.COMMIT_AGENT_PROVIDER_ENV, "stub-wave2")
        resolved = providers.resolve_commit_agent_provider()
        assert resolved.name == "stub-wave2"
        assert "stub-wave2" in providers.known_commit_agent_providers()
    finally:
        providers._FACTORIES.pop("stub-wave2", None)


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


# ---------------------------------------------------------------------------
# Issue #116 — Actions-only commit must not sweep pre-dirty files
# ---------------------------------------------------------------------------


def test_actions_only_does_not_sweep_predirty_lockfile(tmp_path: Path) -> None:
    """#116: an actions-only pin bump must stage only the workflow file, not a
    pre-existing dirty yarn.lock that was never touched by this run."""
    from repo_ops_models import ActionBump

    _init_git_repo(tmp_path)
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text("on: push\njobs: {}\n", encoding="utf-8")
    # Commit a baseline yarn.lock so it is tracked.
    yarn_lock = tmp_path / "yarn.lock"
    yarn_lock.write_text("# baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    # Operator-kept dirty yarn.lock (1,232 lines untouched by this run).
    yarn_lock.write_text("# operator edit\n" + "x\n" * 1231, encoding="utf-8")
    # Run bumps only the workflow.
    wf.write_text("on: push  # bumped\njobs: {}\n", encoding="utf-8")

    bundle = RepoBundle(
        git_repo=tmp_path,
        label="ci-only",
        workspaces=[],
        actions=[
            ActionBump(
                workflow=".github/workflows/ci.yml",
                action="actions/checkout",
                pin="v4",
                latest="v6",
            )
        ],
    )
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason is None
    assert plan.files == [".github/workflows/ci.yml"]
    assert "yarn.lock" not in plan.files


# ---------------------------------------------------------------------------
# Issue #116 — prefix-boundary bug in upgrade_scoped_paths
# ---------------------------------------------------------------------------


def test_upgrade_scoped_paths_prefix_boundary(tmp_path: Path) -> None:
    """#116: 'packages/foo' prefix must not match 'packages/foobar/yarn.lock'."""
    _init_git_repo(tmp_path)
    foo_dir = tmp_path / "packages" / "foo"
    foo_dir.mkdir(parents=True)
    foobar_dir = tmp_path / "packages" / "foobar"
    foobar_dir.mkdir(parents=True)

    ws = Workspace(
        path=foo_dir,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="foo",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    bundle = RepoBundle(git_repo=tmp_path, label="boundary", workspaces=[wr])

    # Porcelain includes the sibling workspace's lockfile which must NOT be staged.
    porcelain = [
        "packages/foo/yarn.lock",
        "packages/foobar/yarn.lock",  # sibling — must be excluded
    ]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "packages/foo/yarn.lock" in scoped
    assert "packages/foobar/yarn.lock" not in scoped


def test_upgrade_scoped_paths_root_exact_path(tmp_path: Path) -> None:
    """Root-workspace name match must not fire for a subdirectory path.

    'yarn.lock' at root must be included; 'sub/yarn.lock' must not, because the
    active workspace is at the repo root and the subdirectory is unrelated.
    """
    _init_git_repo(tmp_path)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="root",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    bundle = RepoBundle(git_repo=tmp_path, label="root", workspaces=[wr])

    porcelain = ["yarn.lock", "sub/yarn.lock"]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "yarn.lock" in scoped
    assert "sub/yarn.lock" not in scoped


# ---------------------------------------------------------------------------
# Issue #153 — nested yarn-workspace package.json dropped from root-scoped commit
# ---------------------------------------------------------------------------


def test_upgrade_scoped_paths_includes_nested_yarn_workspace_member(
    tmp_path: Path,
) -> None:
    """A root-scanned yarn workspaces monorepo whose install resolves a bump
    into a nested member's package.json must stage that manifest alongside the
    root yarn.lock — not silently drop it and leave lockfile/manifest out of
    sync on main. Issue #153."""
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","workspaces":["apps/*"]}', encoding="utf-8"
    )
    (tmp_path / "apps" / "web").mkdir(parents=True)

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="root",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    bundle = RepoBundle(git_repo=tmp_path, label="root", workspaces=[wr])

    porcelain = ["yarn.lock", "apps/web/package.json"]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "yarn.lock" in scoped
    assert "apps/web/package.json" in scoped


def test_upgrade_scoped_paths_excludes_non_member_nested_package_json(
    tmp_path: Path,
) -> None:
    """A dirty package.json outside the monorepo's declared workspace globs must
    never be swept into the commit — only declared members are eligible."""
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","workspaces":["apps/*"]}', encoding="utf-8"
    )
    (tmp_path / "vendor" / "other").mkdir(parents=True)

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="root",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    bundle = RepoBundle(git_repo=tmp_path, label="root", workspaces=[wr])

    porcelain = ["yarn.lock", "vendor/other/package.json"]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "yarn.lock" in scoped
    assert "vendor/other/package.json" not in scoped


# ---------------------------------------------------------------------------
# Issue #154 — remediation-written files (e.g. biome_schema's biome.json)
# dropped from root-workspace commits
# ---------------------------------------------------------------------------


def test_upgrade_scoped_paths_includes_applied_remediation_file(
    tmp_path: Path,
) -> None:
    """A root-scanned workspace's applied biome_schema remediation rewrites
    biome.json's $schema URL — a third file class outside the manager's own
    manifest/lockfile — and it must be staged alongside the lockfile rather
    than silently dropped and left dirty on main. Issue #154."""
    _init_git_repo(tmp_path)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="root",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    wr.remediations = [
        {
            "id": "biome_schema",
            "status": "applied",
            "detail": "biome migrate --write (ok)",
            "files": ["biome.json"],
        }
    ]
    bundle = RepoBundle(git_repo=tmp_path, label="root", workspaces=[wr])

    porcelain = ["yarn.lock", "biome.json"]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "yarn.lock" in scoped
    assert "biome.json" in scoped


def test_upgrade_scoped_paths_excludes_unapplied_remediation_file(
    tmp_path: Path,
) -> None:
    """A remediation that only reported (plan mode) or failed to apply must not
    stage the file it would have touched — nothing was actually written."""
    _init_git_repo(tmp_path)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="root",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    wr.remediations = [
        {
            "id": "biome_schema",
            "status": "pending",
            "detail": "biome migrate --write",
            "files": ["biome.json"],
        }
    ]
    bundle = RepoBundle(git_repo=tmp_path, label="root", workspaces=[wr])

    # biome.json is pre-existing dirt (unrelated to this run) — must not be swept in.
    porcelain = ["yarn.lock", "biome.json"]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "yarn.lock" in scoped
    assert "biome.json" not in scoped


def test_upgrade_scoped_paths_includes_nested_remediation_file(
    tmp_path: Path,
) -> None:
    """A subdirectory workspace's applied remediation file must also be staged
    by the general remediation-files match (not just the prefix match), and
    must respect the workspace's own directory even outside its prefix."""
    _init_git_repo(tmp_path)
    sub = tmp_path / "frontend"
    sub.mkdir()

    ws = Workspace(
        path=sub,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="frontend",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("pkg", "1", "2")]
    wr.remediations = [
        {
            "id": "biome_schema",
            "status": "applied",
            "detail": "biome migrate --write (ok)",
            "files": ["biome.json"],
        }
    ]
    bundle = RepoBundle(git_repo=tmp_path, label="root", workspaces=[wr])

    porcelain = ["frontend/yarn.lock", "frontend/biome.json"]
    scoped = post_apply_commit.upgrade_scoped_paths(bundle, porcelain)
    assert "frontend/yarn.lock" in scoped
    assert "frontend/biome.json" in scoped


# ---------------------------------------------------------------------------
# Issue #116 — classify_commit_subject multi-ecosystem
# ---------------------------------------------------------------------------


def test_classify_mixed_node_and_ci_pins() -> None:
    """#116: yarn.lock + workflow file must not collapse to chore(ci)-only subject."""
    files = ["yarn.lock", ".github/workflows/ci.yml"]
    subject = post_apply_commit.classify_commit_subject(files)
    # Must mention both Node lockfiles and CI pins.
    assert "Node" in subject
    assert "CI" in subject
    # Must NOT be pure chore(ci) (that was the misattribution bug).
    assert subject != "chore(ci): bump GitHub Actions pins"


def test_classify_go_and_node_mixed() -> None:
    """Multi-ecosystem deps-only commit should enumerate both."""
    files = ["go.mod", "go.sum", "yarn.lock"]
    subject = post_apply_commit.classify_commit_subject(files)
    assert "Go" in subject
    assert "Node" in subject


def test_classify_single_ecosystem_unchanged() -> None:
    """Single-ecosystem commits must keep exact existing subject strings."""
    assert post_apply_commit.classify_commit_subject(["go.mod", "go.sum"]) == (
        "chore(deps): update Go modules"
    )
    assert post_apply_commit.classify_commit_subject(["yarn.lock"]) == (
        "chore(deps): update Node lockfiles and manifests"
    )
    assert post_apply_commit.classify_commit_subject([".github/workflows/ci.yml"]) == (
        "chore(ci): bump GitHub Actions pins"
    )


# ---------------------------------------------------------------------------
# Issue #116 — run_post_apply_commits parallelism
# ---------------------------------------------------------------------------


def test_run_post_apply_commits_parallel_outcomes_sorted(tmp_path: Path) -> None:
    """Outcomes are returned in label order regardless of execution order."""
    # Build three minimal bundles — each in its own git repo directory.
    paths = [tmp_path / f"repo{i}" for i in range(3)]
    bundles: dict[Path, RepoBundle] = {}
    for p in paths:
        p.mkdir()
        _init_git_repo(p)
        ws = Workspace(
            path=p, ecosystem="node", manager="npm", git_repo=p, label=p.name
        )
        wr = WorkspaceResult(workspace=ws, status="ok")
        # No changes → each will skip as "no upgrade-scoped changes to commit".
        bundles[p] = RepoBundle(git_repo=p, label=p.name, workspaces=[wr])

    outcomes, _ = post_apply_commit.run_post_apply_commits(
        bundles,
        mode="conventional",
        dry_run=True,
        quiet=True,
        max_workers=3,
    )
    labels = [o.label for o in outcomes]
    assert labels == sorted(labels)


def test_run_post_apply_commits_max_workers_accepted(tmp_path: Path) -> None:
    """max_workers parameter is accepted without error."""
    _init_git_repo(tmp_path)
    ws = Workspace(
        path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label="w"
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    bundle = RepoBundle(git_repo=tmp_path, label="w", workspaces=[wr])
    outcomes, _ = post_apply_commit.run_post_apply_commits(
        {tmp_path: bundle},
        mode="conventional",
        dry_run=True,
        quiet=True,
        max_workers=1,
    )
    assert len(outcomes) == 1


def test_collect_changes_no_diff_reports_nothing(tmp_path: Path) -> None:
    """#113 — a clean git diff means no changes were applied. The removed
    checker fallback used to re-run the ecosystem checker here and report
    still-available updates as applied (ghost upgrades)."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="root",
    )

    def _fake_run(
        cmd: list[str], cwd: object, **kwargs: object
    ) -> CompletedProcess[str]:
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    assert changes == [], f"clean diff must yield no changes, got {changes}"


def test_pdm_root_lock_and_manifest_in_commit_scope(tmp_path: Path) -> None:
    """pdm.lock + pyproject.toml are staged for root pdm workspaces (F049)."""
    _init_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (tmp_path / "pdm.lock").write_text("[[package]]\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "pdm.lock").write_text("[[package]]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.1"\n', encoding="utf-8"
    )
    ws = Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pdm",
        git_repo=tmp_path,
        label="demo",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("requests", "2.0", "2.1")]
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason is None
    assert set(plan.files) == {"pdm.lock", "pyproject.toml"}


def test_pipenv_root_lock_and_manifest_in_commit_scope(tmp_path: Path) -> None:
    """Pipfile.lock + Pipfile are staged for root pipenv workspaces (F049)."""
    _init_git_repo(tmp_path)
    (tmp_path / "Pipfile").write_text('[packages]\nrequests = "*"\n', encoding="utf-8")
    (tmp_path / "Pipfile.lock").write_text('{"default":{}}', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "Pipfile.lock").write_text(
        '{"default":{"requests":{}}}', encoding="utf-8"
    )
    (tmp_path / "Pipfile").write_text(
        '[packages]\nrequests = "==2.1"\n', encoding="utf-8"
    )
    ws = Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pipenv",
        git_repo=tmp_path,
        label="demo",
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    wr.changes = [Change("requests", "2.0", "2.1")]
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    plan = post_apply_commit.plan_repo_commit(
        bundle, mode="conventional", allow_dirty=False, commit_on_fail=False
    )
    assert plan.skipped_reason is None
    assert set(plan.files) == {"Pipfile.lock", "Pipfile"}
