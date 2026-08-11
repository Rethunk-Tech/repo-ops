"""End-to-end tests: [tool.repo-ops] config file keys reach run.py / os.environ.

Task 1: resolve_setting / apply_config_to_env wiring (#TASK-1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import repo_ops_go
import repo_ops_go.go as go_mod
import repo_ops_upgrade.run as run_mod
import repo_ops_upgrade.verify as verify_mod
from repo_ops_core.config import apply_config_to_env, resolve_setting, set_ambient_env
from repo_ops_core.constants import (
    resolve_find_timeout,
    resolve_git_timeout,
    resolve_timeout_apply,
    resolve_timeout_check,
    resolve_timeout_verify,
)
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import commit_agent_cursor, commit_agent_providers

# ---------------------------------------------------------------------------
# apply_config_to_env unit tests
# ---------------------------------------------------------------------------


def test_apply_config_to_env_sets_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """String value from repo config reaches os.environ."""
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    apply_config_to_env(repo_cfg={"go_upgrade_mode": "explicit"})
    assert os.environ["REPO_OPS_GO_UPGRADE_MODE"] == "explicit"


def test_apply_config_to_env_bool_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """TOML boolean true → '1' in os.environ."""
    monkeypatch.delenv("REPO_OPS_YARN_TARGETED_BUMP", raising=False)
    apply_config_to_env(repo_cfg={"yarn_targeted_bump": True})
    assert os.environ["REPO_OPS_YARN_TARGETED_BUMP"] == "1"


def test_apply_config_to_env_bool_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """TOML boolean false → '0' in os.environ."""
    monkeypatch.delenv("REPO_OPS_BUN_UPDATE_LATEST", raising=False)
    apply_config_to_env(repo_cfg={"bun_update_latest": False})
    assert os.environ["REPO_OPS_BUN_UPDATE_LATEST"] == "0"


def test_apply_config_to_env_skips_if_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing env var is not overwritten (env > config)."""
    monkeypatch.setenv("REPO_OPS_GO_UPGRADE_MODE", "patch")
    apply_config_to_env(repo_cfg={"go_upgrade_mode": "explicit"})
    assert os.environ["REPO_OPS_GO_UPGRADE_MODE"] == "patch"


def test_apply_config_to_env_global_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When repo config is absent, global config fills the env var."""
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    apply_config_to_env(global_cfg={"go_upgrade_mode": "mvs"}, repo_cfg={})
    assert os.environ["REPO_OPS_GO_UPGRADE_MODE"] == "mvs"


def test_apply_config_to_env_repo_wins_over_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-repo config key overrides the same key in global config."""
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    apply_config_to_env(
        global_cfg={"go_upgrade_mode": "patch"},
        repo_cfg={"go_upgrade_mode": "explicit"},
    )
    assert os.environ["REPO_OPS_GO_UPGRADE_MODE"] == "explicit"


def test_apply_config_to_env_integer_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integer config values (e.g. yarn_max_concurrency) are stringified."""
    monkeypatch.delenv("REPO_OPS_YARN_MAX_CONCURRENCY", raising=False)
    apply_config_to_env(repo_cfg={"yarn_max_concurrency": 4})
    assert os.environ["REPO_OPS_YARN_MAX_CONCURRENCY"] == "4"


def test_apply_config_to_env_none_value_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config key present but None → env var not set (no accidental 'None' string)."""
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    apply_config_to_env(repo_cfg={"go_upgrade_mode": None})
    assert "REPO_OPS_GO_UPGRADE_MODE" not in os.environ


def test_resolve_setting_uses_ambient_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected process environment does not replace the launch snapshot."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_LINT_LEVEL", "injected")
    assert (
        resolve_setting(
            "actions_lint_level",
            global_cfg={"actions_lint_level": "global"},
            repo_cfg={"actions_lint_level": "repo"},
            env={"REPO_OPS_ACTIONS_LINT_LEVEL": "caller"},
        )
        == "caller"
    )


def test_commit_agent_model_uses_repo_config_over_injected_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit-agent model resolution ignores config injected after launch."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\ncommit_agent_model = "repo-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REPO_OPS_COMMIT_AGENT_MODEL", "injected-model")
    commit_agent_providers.set_ambient_env({})
    try:
        assert commit_agent_cursor._resolve_agent_model(tmp_path) == "repo-model"
    finally:
        commit_agent_providers.set_ambient_env(None)


# ---------------------------------------------------------------------------
# run_upgrade integration tests: config file → run body
# ---------------------------------------------------------------------------


def test_run_upgrade_jobs_from_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jobs = N in [tool.repo-ops] sets args.jobs for the run (no env var channel)."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.repo-ops]\njobs = 3\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    captured: list[int] = []

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured.append(args.jobs)
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    rc = run_mod.run_upgrade(apply=False, argv=["--flat", str(tmp_path)])
    assert rc == 0
    assert captured == [3]


def test_run_upgrade_go_upgrade_mode_from_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """go_upgrade_mode in [tool.repo-ops] reaches os.environ during the run."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\ngo_upgrade_mode = "explicit"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)

    captured: dict[str, str | None] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["go_upgrade_mode"] = os.environ.get("REPO_OPS_GO_UPGRADE_MODE")
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=False, argv=["--flat", str(tmp_path)])
    assert captured["go_upgrade_mode"] == "explicit"


def test_run_upgrade_env_wins_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User-set env var takes precedence over [tool.repo-ops] config file key."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\ngo_upgrade_mode = "explicit"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO_OPS_GO_UPGRADE_MODE", "patch")

    captured: dict[str, str | None] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["go_upgrade_mode"] = os.environ.get("REPO_OPS_GO_UPGRADE_MODE")
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=False, argv=["--flat", str(tmp_path)])
    # env var must be preserved, not overwritten by config
    assert captured["go_upgrade_mode"] == "patch"


def test_go_upgrade_mode_cli_value_overrides_ambient_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process value applied after launch wins over the ambient snapshot."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\ngo_upgrade_mode = "mvs"\n',
        encoding="utf-8",
    )
    workspace = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="repo",
    )
    monkeypatch.setenv("REPO_OPS_GO_UPGRADE_MODE", "explicit")
    set_ambient_env({"REPO_OPS_GO_UPGRADE_MODE": "patch"})
    try:
        assert go_mod._go_upgrade_mode(workspace) == "explicit"
    finally:
        set_ambient_env(None)


def test_fleet_run_resolves_actions_config_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fleet runs use each bundle's action settings instead of the launch config."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
    (launcher / "pyproject.toml").write_text(
        '[tool.repo-ops]\nactions_lint_level = "launcher"\n'
        'actions_policy_level = "launcher"\n',
        encoding="utf-8",
    )
    (repo_a / "pyproject.toml").write_text(
        '[tool.repo-ops]\nactions_lint_level = "block"\n'
        'actions_policy_level = "warn"\n',
        encoding="utf-8",
    )
    (repo_b / "pyproject.toml").write_text(
        '[tool.repo-ops]\nactions_lint_level = "off"\n'
        'actions_policy_level = "report"\n',
        encoding="utf-8",
    )

    workspaces = [
        Workspace(
            path=repo_a,
            ecosystem="actions",
            manager="actions",
            git_repo=repo_a,
            label="repo-a",
        ),
        Workspace(
            path=repo_b,
            ecosystem="actions",
            manager="actions",
            git_repo=repo_b,
            label="repo-b",
        ),
    ]
    bundles = {
        workspace.git_repo: RepoBundle(
            git_repo=workspace.git_repo,
            label=workspace.label,
            workspaces=[WorkspaceResult(workspace)],
        )
        for workspace in workspaces
    }
    captured: dict[Path, tuple[object, object]] = {}

    monkeypatch.chdir(launcher)
    monkeypatch.delenv("REPO_OPS_ACTIONS_LINT_LEVEL", raising=False)
    monkeypatch.delenv("REPO_OPS_ACTIONS_POLICY_LEVEL", raising=False)
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
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "merge_action_only_bundles",
        lambda grouped, roots, follow_symlinks=False: grouped,
    )
    monkeypatch.setattr(
        run_mod.orchestration,
        "process_workspace",
        lambda workspace_result, **kwargs: workspace_result,
    )

    def fake_scan(repo_path: Path, **kwargs: object) -> tuple[list, int]:
        captured[repo_path] = (kwargs["lint_level"], kwargs["policy_level"])
        return [], 0

    monkeypatch.setattr(run_mod.actions, "scan_actions", fake_scan)
    monkeypatch.setattr(
        run_mod.report, "print_json_report", lambda *args, **kwargs: None
    )

    rc = run_mod.run_upgrade(
        apply=False,
        argv=["--flat", "--json", str(repo_a), str(repo_b)],
    )

    assert rc == 0
    assert captured == {
        repo_a: ("block", "warn"),
        repo_b: ("off", "report"),
    }


def test_fleet_run_resolves_timeouts_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fleet workers use each checkout's timeout settings."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
    (launcher / "pyproject.toml").write_text(
        "[tool.repo-ops]\ntimeout_check = 99\n", encoding="utf-8"
    )
    (repo_a / "pyproject.toml").write_text(
        "[tool.repo-ops]\n"
        "timeout_check = 11\n"
        "timeout_apply = 12\n"
        "timeout_verify = 13\n"
        "find_timeout = 14\n"
        "git_timeout = 15\n",
        encoding="utf-8",
    )
    (repo_b / "pyproject.toml").write_text(
        "[tool.repo-ops]\n"
        "timeout_check = 21\n"
        "timeout_apply = 22\n"
        "timeout_verify = 23\n"
        "find_timeout = 24\n"
        "git_timeout = 25\n",
        encoding="utf-8",
    )

    workspaces = [
        Workspace(
            path=path,
            ecosystem="actions",
            manager="actions",
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
    captured: dict[Path, tuple[int, int, int, float, float]] = {}

    monkeypatch.chdir(launcher)
    for env_var in (
        "REPO_OPS_TIMEOUT_CHECK",
        "REPO_OPS_TIMEOUT_APPLY",
        "REPO_OPS_TIMEOUT_VERIFY",
        "REPO_OPS_FIND_TIMEOUT",
        "REPO_OPS_GIT_TIMEOUT",
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

    def capture_timeouts(workspace_result: WorkspaceResult, **kwargs: object):
        workspace = workspace_result.workspace
        captured[workspace.git_repo] = (
            resolve_timeout_check(),
            resolve_timeout_apply(),
            resolve_timeout_verify(),
            resolve_find_timeout(),
            resolve_git_timeout(),
        )
        return workspace_result

    monkeypatch.setattr(
        run_mod.orchestration,
        "process_workspace",
        capture_timeouts,
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
        repo_a: (11, 12, 13, 14.0, 15.0),
        repo_b: (21, 22, 23, 24.0, 25.0),
    }


def test_node_verify_script_uses_ambient_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected process values do not override the caller environment snapshot."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nnode_verify_script = "configured"\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"caller": "echo caller", "configured": "echo config"}}),
        encoding="utf-8",
    )
    workspace = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="repo",
    )
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path, timeout: int = 0):
        commands.append(cmd)
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setenv("REPO_OPS_NODE_VERIFY_SCRIPT", "injected")
    set_ambient_env({"REPO_OPS_NODE_VERIFY_SCRIPT": "caller"})
    monkeypatch.setattr(verify_mod, "run", fake_run)
    try:
        assert verify_mod.verify_node(workspace) is None
    finally:
        set_ambient_env(None)

    assert commands == [["npm", "ci"], ["npm", "run", "caller"]]


def test_fleet_run_resolves_go_upgrade_mode_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fleet runs use each checkout's Go upgrade mode."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
    (launcher / "pyproject.toml").write_text(
        '[tool.repo-ops]\ngo_upgrade_mode = "mvs"\n',
        encoding="utf-8",
    )
    (repo_a / "pyproject.toml").write_text(
        "[tool.repo-ops]\n"
        'go_upgrade_mode = "explicit"\n'
        'go_directive = "1.25"\n'
        'go_toolchain = "go1.25.3"\n',
        encoding="utf-8",
    )
    (repo_b / "pyproject.toml").write_text(
        "[tool.repo-ops]\n"
        'go_upgrade_mode = "patch"\n'
        'go_directive = "1.26"\n'
        'go_toolchain = "1.26.4"\n',
        encoding="utf-8",
    )
    global_config_dir = tmp_path / "config" / "repo-ops"
    global_config_dir.mkdir(parents=True)
    (global_config_dir / "config.toml").write_text(
        'go_upgrade_mode = "mvs"\ngo_directive = "1.90"\ngo_toolchain = "go1.90.1"\n',
        encoding="utf-8",
    )
    for path in (repo_a, repo_b):
        (path / "go.mod").write_text(
            "module example.com/main\n\ngo 1.24\n\n"
            "require (\n"
            "\texample.com/patch v1.2.3\n"
            "\texample.com/minor v1.2.3\n"
            ")\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "--quiet", str(path)], check=True)
        subprocess.run(
            ["git", "-C", str(path), "add", "go.mod", "pyproject.toml"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "-c",
                "user.name=repo-ops-test",
                "-c",
                "user.email=repo-ops-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            check=True,
        )

    workspaces = [
        Workspace(
            path=repo_a,
            ecosystem="go",
            manager="go",
            git_repo=repo_a,
            label="repo-a",
        ),
        Workspace(
            path=repo_b,
            ecosystem="go",
            manager="go",
            git_repo=repo_b,
            label="repo-b",
        ),
    ]
    bundles = {
        workspace.git_repo: RepoBundle(
            git_repo=workspace.git_repo,
            label=workspace.label,
            workspaces=[WorkspaceResult(workspace, status="pending")],
        )
        for workspace in workspaces
    }
    captured: dict[Path, list[tuple[str, str]]] = {}
    go_list_stdout = "\n".join(
        json.dumps(module)
        for module in (
            {
                "Path": "example.com/patch",
                "Version": "v1.2.3",
                "Update": {
                    "Path": "example.com/patch",
                    "Version": "v1.2.4",
                },
            },
            {
                "Path": "example.com/minor",
                "Version": "v1.2.3",
                "Update": {
                    "Path": "example.com/minor",
                    "Version": "v1.3.0",
                },
            },
        )
    )
    go_mod_stdout = json.dumps(
        {
            "Require": [
                {"Path": "example.com/patch", "Version": "v1.2.3"},
                {"Path": "example.com/minor", "Version": "v1.2.3"},
            ]
        }
    )
    monkeypatch.chdir(launcher)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    monkeypatch.delenv("REPO_OPS_GO_DIRECTIVE", raising=False)
    monkeypatch.delenv("REPO_OPS_GO_TOOLCHAIN", raising=False)
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

    def fake_go_run(cmd: list[str], cwd: Path, timeout: int = 0):
        if cmd == ["go", "mod", "edit", "-json"]:
            stdout = go_mod_stdout
        elif cmd == ["go", "list", "-m", "-u", "-json", "all"]:
            stdout = go_list_stdout
        else:
            raise AssertionError(f"unexpected command: {cmd}")
        return type("Proc", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(go_mod, "run", fake_go_run)
    repo_ops_go.register()

    original_check_workspace = run_mod.orchestration.ecosystems.check_workspace

    def capture_check_workspace(workspace: Workspace, **kwargs: object):
        result = original_check_workspace(workspace, **kwargs)
        captured[workspace.git_repo] = [
            (change.name, change.to) for change in result.changes
        ]
        return result

    monkeypatch.setattr(
        run_mod.orchestration.ecosystems,
        "check_workspace",
        capture_check_workspace,
    )
    monkeypatch.setattr(
        run_mod.report, "print_json_report", lambda *args, **kwargs: None
    )

    rc = run_mod.run_upgrade(
        apply=False,
        argv=[
            "--flat",
            "--json",
            "--skip-actions",
            str(repo_a),
            str(repo_b),
        ],
    )

    assert rc == 1
    assert captured == {
        repo_a: [
            ("example.com/patch", "v1.2.4"),
            ("example.com/minor", "v1.3.0"),
            ("go directive", "1.25"),
            ("go toolchain", "1.25.3"),
        ],
        repo_b: [
            ("example.com/patch", "v1.2.4"),
            ("go directive", "1.26"),
            ("go toolchain", "1.26.4"),
        ],
    }


def test_fleet_cli_go_upgrade_mode_overrides_checkout_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI Go mode overrides each checkout's config value."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
    (repo_a / "pyproject.toml").write_text(
        '[tool.repo-ops]\ngo_upgrade_mode = "explicit"\n',
        encoding="utf-8",
    )
    (repo_b / "pyproject.toml").write_text(
        '[tool.repo-ops]\ngo_upgrade_mode = "patch"\n',
        encoding="utf-8",
    )

    workspaces = [
        Workspace(
            path=repo_a,
            ecosystem="go",
            manager="go",
            git_repo=repo_a,
            label="repo-a",
        ),
        Workspace(
            path=repo_b,
            ecosystem="go",
            manager="go",
            git_repo=repo_b,
            label="repo-b",
        ),
    ]
    bundles = {
        workspace.git_repo: RepoBundle(
            git_repo=workspace.git_repo,
            label=workspace.label,
            workspaces=[WorkspaceResult(workspace)],
        )
        for workspace in workspaces
    }
    captured: dict[Path, str] = {}

    monkeypatch.chdir(launcher)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
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
    monkeypatch.setattr(
        run_mod.orchestration,
        "process_workspace",
        lambda workspace_result, **kwargs: (
            captured.__setitem__(
                workspace_result.workspace.git_repo,
                go_mod._go_upgrade_mode(workspace_result.workspace),
            ),
            workspace_result,
        )[1],
    )
    monkeypatch.setattr(
        run_mod.report, "print_json_report", lambda *args, **kwargs: None
    )

    rc = run_mod.run_upgrade(
        apply=False,
        argv=[
            "--flat",
            "--json",
            "--skip-actions",
            "--go-upgrade-mode",
            "mvs",
            str(repo_a),
            str(repo_b),
        ],
    )

    assert rc == 0
    assert captured == {repo_a: "mvs", repo_b: "mvs"}


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_run_upgrade_global_config_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global config.toml key is applied when no pyproject.toml or CLI override."""
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        'actions_lint_level = "off"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_ACTIONS_LINT_LEVEL", raising=False)

    scan_dir = tmp_path / "repo"
    scan_dir.mkdir()

    captured: dict[str, str | None] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["lint_level"] = os.environ.get("REPO_OPS_ACTIONS_LINT_LEVEL")
        return 0

    monkeypatch.chdir(scan_dir)
    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=False, argv=["--flat", str(scan_dir)])
    assert captured["lint_level"] == "off"


# ---------------------------------------------------------------------------
# Task A residuals: exclude_patterns + git_timeout (#14)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_exclude_from_pyproject_reaches_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config-file 'exclude' list is injected into args.exclude_patterns."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nexclude = ["vendor", "node_modules"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["exclude_patterns"] = getattr(args, "exclude_patterns", None)
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=False, argv=["--flat", str(tmp_path)])
    assert captured["exclude_patterns"] == ["vendor", "node_modules"]


def test_cli_exclude_wins_over_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI --exclude takes precedence over config-file 'exclude'."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nexclude = ["config_vendor"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["exclude_patterns"] = getattr(args, "exclude_patterns", None)
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(
        apply=False, argv=["--flat", "--exclude", "cli_vendor", str(tmp_path)]
    )
    # CLI value must win; config-file value must be ignored
    assert captured["exclude_patterns"] == ["cli_vendor"]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_git_timeout_from_config_reaches_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config-file git_timeout is injected into REPO_OPS_GIT_TIMEOUT env var."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.repo-ops]\ngit_timeout = 30\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPO_OPS_GIT_TIMEOUT", raising=False)

    captured: dict[str, str | None] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["git_timeout"] = os.environ.get("REPO_OPS_GIT_TIMEOUT")
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=False, argv=["--flat", str(tmp_path)])
    assert captured["git_timeout"] == "30"


def test_git_timeout_env_wins_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing REPO_OPS_GIT_TIMEOUT env var is not overwritten by config."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.repo-ops]\ngit_timeout = 30\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO_OPS_GIT_TIMEOUT", "5")

    captured: dict[str, str | None] = {}

    def _fake_body(*, apply: bool, args, roots, follow_symlinks: bool = False) -> int:
        captured["git_timeout"] = os.environ.get("REPO_OPS_GIT_TIMEOUT")
        return 0

    monkeypatch.setattr(run_mod, "_run_upgrade_body", _fake_body)
    run_mod.run_upgrade(apply=False, argv=["--flat", str(tmp_path)])
    assert captured["git_timeout"] == "5"
