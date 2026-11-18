"""A repo dirty only in its own manifests/lockfiles is upgraded, not skipped."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core import state as state_mod
from repo_ops_models import DIRTY_SKIP_MARKER, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import orchestration
from repo_ops_upgrade.post_apply_commit import plan_repo_commit


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, init_git_repo, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_mod._CACHE = None
    state_mod._DIRTY = False
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    work = tmp_path / "repo"
    work.mkdir()
    init_git_repo(work)
    (work / "package.json").write_text('{"dependencies":{"turbo":"1.0.0"}}\n')
    (work / "bun.lock").write_text("turbo 1.0.0\n")
    (work / "src").mkdir()
    (work / "src" / "index.js").write_text("export const x = 1;\n")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "init")
    return work


def _bump(ws: Workspace) -> list:
    (ws.path / "package.json").write_text('{"dependencies":{"turbo":"2.0.0"}}\n')
    (ws.path / "bun.lock").write_text("turbo 2.0.0\n")
    return []


def _process(repo: Path, *, verify_error: str = "") -> WorkspaceResult:
    ws = Workspace(path=repo, ecosystem="node", manager="bun", git_repo=repo, label="r")
    with (
        patch.object(orchestration, "should_exclude", return_value=False),
        patch.object(orchestration.ecosystems, "apply_workspace", side_effect=_bump),
        patch.object(orchestration, "merge_toolchains", side_effect=lambda c, b, *a: (c, b)),
        patch.object(orchestration, "sync_soup_versions"),
        patch.object(orchestration, "overrides_audit", return_value=[]),
        patch.object(orchestration, "dispatch_remediations"),
        patch.object(orchestration.verify_mod, "verify_workspace", return_value=verify_error),
    ):
        return orchestration.process_workspace(
            WorkspaceResult(workspace=ws),
            do_apply=True,
            do_verify=True,
            allow_dirty=False,
            on_verify_fail="revert",
            emit_progress=False,
        )


def test_manifest_and_lockfile_only_dirt_is_upgraded_and_committed(repo: Path) -> None:
    (repo / "package.json").write_text('{"dependencies":{"turbo":"1.5.0"}}\n')
    (repo / "bun.lock").write_text("turbo 1.5.0\n")

    out = _process(repo)

    assert out.status == "ok", out.error
    assert out.adopted_dirty == frozenset({"package.json", "bun.lock"})
    assert (repo / "package.json").read_text() == '{"dependencies":{"turbo":"2.0.0"}}\n'
    bundle = RepoBundle(git_repo=repo, label="r", workspaces=[out])
    bundle.pre_dirty = frozenset({"package.json", "bun.lock"})
    plan = plan_repo_commit(bundle)
    assert sorted(plan.files) == ["bun.lock", "package.json"]


def test_source_file_dirt_still_skips_with_marker(repo: Path) -> None:
    (repo / "src" / "index.js").write_text("export const x = 2;\n")

    out = _process(repo)

    assert out.status == "skip"
    assert out.error is not None and DIRTY_SKIP_MARKER in out.error
    assert (repo / "package.json").read_text() == '{"dependencies":{"turbo":"1.0.0"}}\n'


def test_verify_failure_on_adopted_repo_leaves_tree_at_head(repo: Path) -> None:
    (repo / "package.json").write_text('{"dependencies":{"turbo":"1.5.0"}}\n')
    (repo / "bun.lock").write_text("turbo 1.5.0\n")

    out = _process(repo, verify_error="gate failed")

    assert out.status == "fail"
    assert out.error is not None and "reverted to HEAD" in out.error
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "package.json").read_text() == '{"dependencies":{"turbo":"1.0.0"}}\n'
    assert (repo / "bun.lock").read_text() == "turbo 1.0.0\n"
