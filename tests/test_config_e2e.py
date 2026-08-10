"""End-to-end tests: [tool.repo-ops] config file keys reach run.py / os.environ.

Task 1: resolve_setting / apply_config_to_env wiring (#TASK-1).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import repo_ops_upgrade.run as run_mod
from repo_ops_core.config import apply_config_to_env
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult

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


def test_fleet_run_resolves_actions_config_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fleet runs use each bundle's action settings instead of the launch config."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
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
