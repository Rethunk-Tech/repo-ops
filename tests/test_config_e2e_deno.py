"""End-to-end coverage for per-checkout Deno upgrade configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
import repo_ops_upgrade.run as run_mod
from repo_ops_js.deno import (
    _deno_targeted_bump_enabled,
    _deno_update_latest_enabled,
)
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult


def test_fleet_run_resolves_deno_upgrade_flags_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
    (launcher / "pyproject.toml").write_text(
        "[tool.repo-ops]\ndeno_update_latest = false\ndeno_targeted_bump = false\n",
        encoding="utf-8",
    )
    (repo_a / "pyproject.toml").write_text(
        "[tool.repo-ops]\ndeno_update_latest = true\ndeno_targeted_bump = true\n",
        encoding="utf-8",
    )
    (repo_b / "pyproject.toml").write_text(
        "[tool.repo-ops]\ndeno_update_latest = false\ndeno_targeted_bump = false\n",
        encoding="utf-8",
    )

    workspaces = [
        Workspace(
            path=path,
            ecosystem="deno",
            manager="deno",
            git_repo=path,
            label=path.name,
        )
        for path in (repo_a, repo_b)
    ]
    bundles = {
        workspace.git_repo: RepoBundle(
            git_repo=workspace.git_repo,
            label=workspace.label,
            workspaces=[WorkspaceResult(workspace)],
        )
        for workspace in workspaces
    }
    captured: dict[Path, tuple[bool, bool]] = {}

    monkeypatch.chdir(launcher)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for env_var in (
        "REPO_OPS_DENO_UPDATE_LATEST",
        "REPO_OPS_DENO_TARGETED_BUMP",
    ):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "discover_all",
        lambda *args, **kwargs: workspaces,
    )
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "group_by_repo",
        lambda found, roots: bundles.copy(),
    )

    def capture_flags(workspace_result: WorkspaceResult, **kwargs: object):
        workspace = workspace_result.workspace
        captured[workspace.git_repo] = (
            _deno_update_latest_enabled(workspace),
            _deno_targeted_bump_enabled(workspace),
        )
        return workspace_result

    monkeypatch.setattr(
        run_mod.orchestration,
        "process_workspace",
        capture_flags,
    )
    monkeypatch.setattr(
        run_mod.report, "print_json_report", lambda *args, **kwargs: None
    )

    rc = run_mod.run_upgrade(
        apply=False,
        argv=["--flat", "--json", "--skip-actions", str(repo_a), str(repo_b)],
    )

    assert rc == 0
    assert captured == {
        repo_a: (True, True),
        repo_b: (False, False),
    }
