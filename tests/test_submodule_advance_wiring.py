"""Integration tests for Task #18 submodule tag-advance apply wiring.

These tests verify the WIRING between apply_submodule_tag_advances, the
dirty-tree guard, the --detach checkout, and the commit phase (gitlink
paths included in upgrade_scoped_paths).  Unit tests for the pure-logic
helpers live in test_submodule_tag_advance.py.

Key invariants:
  1. apply_submodule_tag_advances is invoked from the apply flow when
     --submodules is passed (dead-code bug fixed).
  2. The gitlink pointer ends up committed (included in upgrade_scoped_paths)
     not left staged-but-uncommitted.
  3. The dirty-tree guard prevents checkout over uncommitted changes.
  4. Checkout uses --detach refs/tags/<tag> (not bare tag name).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import post_apply_commit
from repo_ops_upgrade.post_apply_commit import (
    CommitPlan,
    upgrade_scoped_paths,
)
from repo_ops_upgrade.submodules import (
    SubmoduleLink,
    SubmoduleTagBump,
    _clear_submodule_tag_cache,
    apply_submodule_tag_advances,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    _clear_submodule_tag_cache()
    yield
    _clear_submodule_tag_cache()


def _make_actionable_bump(
    tmp_path: Path,
    *,
    path: str = "sub",
    url: str = "https://example.com/sub.git",
    current_tag: str = "v1.0.0",
    latest_tag: str = "v1.1.0",
) -> SubmoduleTagBump:
    git_repo = (tmp_path / path).resolve()
    git_repo.mkdir(parents=True, exist_ok=True)
    return SubmoduleTagBump(
        path=path,
        url=url,
        current_tag=current_tag,
        latest_tag=latest_tag,
        parent=tmp_path.resolve(),
        git_repo=git_repo,
    )


def _make_bundle(git_repo: Path, *, with_change: bool = False) -> RepoBundle:
    ws = Workspace(
        path=git_repo / "package.json",
        ecosystem="js",
        manager="npm",
        git_repo=git_repo,
        label=str(git_repo),
    )
    wr = WorkspaceResult(workspace=ws)
    if with_change:
        from repo_ops_models import Change

        wr.changes = [Change(name="foo", from_ver="1.0.0", to="1.1.0")]
    return RepoBundle(
        git_repo=git_repo,
        label=str(git_repo),
        workspaces=[wr],
    )


# ---------------------------------------------------------------------------
# 1. apply_submodule_tag_advances IS invoked from the apply flow
# ---------------------------------------------------------------------------


def test_apply_submodule_tag_advances_invoked_via_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    """apply_submodule_tag_advances is called when --submodules + apply.

    Verifies the dead-code wiring bug (Task #18.1) is fixed: the function
    must be reachable from the normal apply flow.
    """
    import repo_ops_upgrade.run as run_mod
    import repo_ops_upgrade.submodules as sub_mod

    # Set up a fake git repo so the run body has something to discover
    root = tmp_path / "root"
    repo = root / "myrepo"
    repo.mkdir(parents=True)
    init_git_repo(repo, commit=True, filename="file.txt", content="v1\n")

    called_with: list = []

    def _fake_apply(links, *, allow_dirty=False):
        called_with.append((links, allow_dirty))
        return []

    # Patch apply_submodule_tag_advances on the submodules module the run
    # module imports (it accesses via `submodules.apply_submodule_tag_advances`)
    monkeypatch.setattr(sub_mod, "apply_submodule_tag_advances", _fake_apply)

    # Patch the heavy parts so the run doesn't need real workspaces/packages
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")

    def _fake_discover(*args, **kwargs):
        return []

    def _fake_group(*args, **kwargs):
        return {}

    def _fake_merge(*args, **kwargs):
        return {}

    import repo_ops_upgrade.discovery as disc

    monkeypatch.setattr(disc, "discover_all", _fake_discover)
    monkeypatch.setattr(disc, "group_by_repo", _fake_group)
    monkeypatch.setattr(disc, "merge_action_only_bundles", _fake_merge)

    # Submodule discovery returns a link so the advance path is entered
    fake_link = SubmoduleLink(
        path="sub",
        git_repo=(repo / "sub").resolve(),
        parent=repo.resolve(),
        url="https://example.com/sub.git",
    )
    monkeypatch.setattr(
        sub_mod, "discover_submodules_under_roots", lambda *a, **kw: [fake_link]
    )

    # run_upgrade with --submodules + apply (--flat so no two-level walk)
    import contextlib

    with contextlib.suppress(SystemExit):
        run_mod.run_upgrade(
            apply=True,
            argv=["--flat", str(root), "--submodules", "--quiet"],
        )

    assert len(called_with) == 1, (
        "apply_submodule_tag_advances must be called exactly once when "
        "--submodules is passed in apply mode"
    )


# ---------------------------------------------------------------------------
# 2. Gitlink pointer is included in upgrade_scoped_paths (committed, not left dirty)
# ---------------------------------------------------------------------------


def test_upgrade_scoped_paths_includes_gitlink(tmp_path: Path) -> None:
    """upgrade_scoped_paths includes staged gitlink paths from extra_gitlink_paths.

    This verifies that the commit phase picks up pointer bumps rather than
    leaving them staged-but-uncommitted (Task #18.2).
    """
    git_repo = tmp_path / "parent"
    git_repo.mkdir()

    # path=git_repo (directory) → rel=Path(".") → treated as root workspace,
    # so go.sum is in root_wanted via GO_LOCKFILES.
    ws = Workspace(
        path=git_repo,
        ecosystem="go",
        manager="go",
        git_repo=git_repo,
        label="parent",
    )
    wr = WorkspaceResult(workspace=ws)
    from repo_ops_models import Change

    wr.changes = [Change(name="github.com/foo/bar", from_ver="v1.0.0", to="v1.1.0")]
    bundle = RepoBundle(git_repo=git_repo, label="parent", workspaces=[wr])

    # Porcelain shows both the go.sum lockfile and the staged gitlink
    porcelain = ["go.sum", "libs/child"]

    # Without extra_gitlink_paths: gitlink is NOT included
    scoped_without = upgrade_scoped_paths(bundle, porcelain)
    assert "libs/child" not in scoped_without

    # With extra_gitlink_paths: gitlink IS included alongside the lockfile
    scoped_with = upgrade_scoped_paths(
        bundle, porcelain, extra_gitlink_paths={"libs/child"}
    )
    assert "libs/child" in scoped_with
    assert "go.sum" in scoped_with


def test_plan_repo_commit_includes_gitlink_via_extra_paths(
    tmp_path: Path, init_git_repo
) -> None:
    """plan_repo_commit stages gitlink paths passed via extra_gitlink_paths.

    Integration path: gitlink appears in the commit plan's files list so
    execute_commit_plan will run git add on it.
    """
    git_repo = tmp_path / "parent"
    git_repo.mkdir()
    init_git_repo(git_repo, commit=True, filename="file.txt", content="v1\n")

    # Write a file so there's something in the porcelain output
    (git_repo / "go.sum").write_text("module example.com/pkg\n", encoding="utf-8")
    # Stage an untracked submodule dir as a fake gitlink marker
    (git_repo / "libs").mkdir()
    (git_repo / "libs" / "child").mkdir()

    ws = Workspace(
        path=git_repo,
        ecosystem="go",
        manager="go",
        git_repo=git_repo,
        label="parent",
    )
    wr = WorkspaceResult(workspace=ws)
    from repo_ops_models import Change

    wr.changes = [Change(name="github.com/foo/bar", from_ver="v1.0.0", to="v1.1.0")]
    bundle = RepoBundle(git_repo=git_repo, label="parent", workspaces=[wr])

    # Mock porcelain to return our staged files
    gitlink_path = "libs/child"
    with patch(
        "repo_ops_upgrade.post_apply_commit.porcelain_changed_paths",
        return_value=["go.sum", gitlink_path],
    ):
        plan = post_apply_commit.plan_repo_commit(
            bundle,
            mode="conventional",
            allow_dirty=False,
            commit_on_fail=False,
            extra_gitlink_paths={gitlink_path},
        )

    assert plan.skipped_reason is None, (
        f"plan should not be skipped: {plan.skipped_reason}"
    )
    assert gitlink_path in plan.files, "gitlink path must appear in commit plan files"


# ---------------------------------------------------------------------------
# 3. Dirty-tree guard: skip submodule advance when checkout is dirty
# ---------------------------------------------------------------------------


def test_dirty_guard_prevents_checkout_when_dirty(tmp_path: Path, make_proc) -> None:
    """apply_submodule_tag_advances skips a bump when the submodule is dirty.

    A dirty submodule checkout (uncommitted changes) must not be overwritten
    with git checkout — that could destroy work.  The dirty guard (allow_dirty=False)
    must prevent the fetch/checkout/add sequence from running.
    """
    link = SubmoduleLink(
        path="sub",
        git_repo=(tmp_path / "sub").resolve(),
        parent=tmp_path.resolve(),
        url="https://example.com/sub.git",
    )
    (tmp_path / "sub").mkdir()

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag",
            return_value="v1.0.0",
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.0.0", "v1.1.0"],
        ),
        # Repo is dirty
        patch("repo_ops_upgrade.submodules.is_git_dirty", return_value=True),
        patch(
            "repo_ops_upgrade.submodules.run_git", return_value=make_proc()
        ) as mock_git,
    ):
        bumps = apply_submodule_tag_advances([link], allow_dirty=False)

    assert len(bumps) == 1
    assert bumps[0].is_actionable
    # No run_git calls expected: dirty guard must have blocked the advance
    mock_git.assert_not_called()


def test_dirty_guard_allows_checkout_when_allow_dirty(tmp_path: Path, make_proc) -> None:
    """allow_dirty=True bypasses the dirty guard and proceeds with checkout."""
    link = SubmoduleLink(
        path="sub",
        git_repo=(tmp_path / "sub").resolve(),
        parent=tmp_path.resolve(),
        url="https://example.com/sub.git",
    )
    (tmp_path / "sub").mkdir()

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag",
            return_value="v1.0.0",
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.0.0", "v1.1.0"],
        ),
        patch("repo_ops_upgrade.submodules.is_git_dirty", return_value=True),
        patch(
            "repo_ops_upgrade.submodules.run_git", return_value=make_proc()
        ) as mock_git,
    ):
        bumps = apply_submodule_tag_advances([link], allow_dirty=True)

    assert len(bumps) == 1
    assert bumps[0].is_actionable
    # fetch + checkout + add must all be called (dirty guard bypassed)
    assert mock_git.call_count == 3


# ---------------------------------------------------------------------------
# 4. Checkout uses --detach refs/tags/<tag> (not bare tag name)
# ---------------------------------------------------------------------------


def test_checkout_uses_detach_with_full_tag_ref(tmp_path: Path, make_proc) -> None:
    """apply_submodule_tag_advances checkouts with --detach refs/tags/<tag>.

    Bare `git checkout <tag>` is ambiguous when a branch shadows the tag name.
    The fix uses `--detach refs/tags/<tag>` to resolve the ref unambiguously.
    """
    link = SubmoduleLink(
        path="sub",
        git_repo=(tmp_path / "sub").resolve(),
        parent=tmp_path.resolve(),
        url="https://example.com/sub.git",
    )
    (tmp_path / "sub").mkdir()

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag",
            return_value="v1.0.0",
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.0.0", "v1.1.0"],
        ),
        patch("repo_ops_upgrade.submodules.is_git_dirty", return_value=False),
        patch(
            "repo_ops_upgrade.submodules.run_git", return_value=make_proc()
        ) as mock_git,
    ):
        apply_submodule_tag_advances([link], allow_dirty=False)

    assert mock_git.call_count == 3
    calls = mock_git.call_args_list
    # Second call is checkout: must use --detach refs/tags/<tag>
    checkout_call = calls[1]
    checkout_args = checkout_call[0]  # positional args to run_git
    # run_git(submod_dir, "checkout", "--detach", "refs/tags/v1.1.0", timeout=...)
    assert checkout_args[1] == "checkout"
    assert "--detach" in checkout_args, (
        f"--detach missing from checkout args: {checkout_args}"
    )
    assert "refs/tags/v1.1.0" in checkout_args, (
        f"full tag ref missing from checkout args: {checkout_args}"
    )
    # Bare tag name must NOT appear (old ambiguous form)
    assert "v1.1.0" in str(checkout_args)  # present as refs/tags/v1.1.0
    # The old form would be just the tag name as second arg after "checkout"
    assert checkout_args[2] == "--detach", (
        "second arg must be --detach, not the bare tag"
    )


# ---------------------------------------------------------------------------
# 5. run_post_apply_commits passes submodule_gitlink_map through
# ---------------------------------------------------------------------------


def test_run_post_apply_commits_passes_gitlink_map(tmp_path: Path) -> None:
    """run_post_apply_commits threads submodule_gitlink_map to plan_repo_commit.

    The gitlink map must reach upgrade_scoped_paths so pointer bumps are
    included in the commit rather than silently dropped.
    """
    git_repo = tmp_path / "parent"
    git_repo.mkdir()

    bundle = _make_bundle(git_repo, with_change=True)
    bundles = {git_repo: bundle}

    captured_extra: list[set[str] | None] = []

    def _spy_plan(b, *, mode, allow_dirty, commit_on_fail, extra_gitlink_paths=None):
        captured_extra.append(extra_gitlink_paths)
        return CommitPlan(
            git_repo=b.git_repo, label=b.label, message="", skipped_reason="test"
        )

    with patch.object(post_apply_commit, "plan_repo_commit", side_effect=_spy_plan):
        post_apply_commit.run_post_apply_commits(
            bundles,
            mode="conventional",
            quiet=True,
            submodule_gitlink_map={git_repo: {"libs/child"}},
        )

    assert len(captured_extra) == 1
    assert captured_extra[0] == {"libs/child"}, (
        "gitlink paths must be passed through to plan_repo_commit"
    )
