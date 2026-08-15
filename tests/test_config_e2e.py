"""End-to-end tests: [tool.repo-ops] config file keys reach run.py / os.environ.

Task 1: resolve_setting / apply_config_to_env wiring (#TASK-1).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import repo_ops_go.go as go_mod
import repo_ops_upgrade.run as run_mod
import repo_ops_upgrade.verify as verify_mod
from repo_ops_container import container as container_mod
from repo_ops_core.config import apply_config_to_env, resolve_setting, set_ambient_env
from repo_ops_core.constants import resolve_timeout_check
from repo_ops_js.deno import _deno_targeted_bump_enabled, _deno_update_latest_enabled
from repo_ops_js_bun import bun as bun_mod
from repo_ops_js_npm import npm as npm_mod
from repo_ops_js_pnpm import pnpm as pnpm_mod
from repo_ops_js_yarn import yarn as yarn_mod
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import commit_agent_cursor
from repo_ops_upgrade import precommit as precommit_mod
from repo_ops_upgrade import submodules as submodules_mod

# ---------------------------------------------------------------------------
# apply_config_to_env unit tests
# ---------------------------------------------------------------------------


# (env_var, repo_cfg, global_cfg, preset_env, expected) — preset_env pre-seeds
# the var (env > config) before the call; expected None means the var stays unset.
_APPLY_CONFIG_TO_ENV_CASES = [
    (
        "REPO_OPS_GO_UPGRADE_MODE",
        {"go_upgrade_mode": "explicit"},
        None,
        None,
        "explicit",
    ),
    ("REPO_OPS_YARN_TARGETED_BUMP", {"yarn_targeted_bump": True}, None, None, "1"),
    ("REPO_OPS_BUN_UPDATE_LATEST", {"bun_update_latest": False}, None, None, "0"),
    (
        "REPO_OPS_GO_UPGRADE_MODE",
        {"go_upgrade_mode": "explicit"},
        None,
        "patch",
        "patch",
    ),
    ("REPO_OPS_GO_UPGRADE_MODE", {}, {"go_upgrade_mode": "mvs"}, None, "mvs"),
    (
        "REPO_OPS_GO_UPGRADE_MODE",
        {"go_upgrade_mode": "explicit"},
        {"go_upgrade_mode": "patch"},
        None,
        "explicit",
    ),
    ("REPO_OPS_YARN_MAX_CONCURRENCY", {"yarn_max_concurrency": 4}, None, None, "4"),
    ("REPO_OPS_GO_UPGRADE_MODE", {"go_upgrade_mode": None}, None, None, None),
]


@pytest.mark.parametrize(
    "env_var,repo_cfg,global_cfg,preset_env,expected",
    _APPLY_CONFIG_TO_ENV_CASES,
    ids=[
        "string-value",
        "bool-true",
        "bool-false",
        "skip-if-already-set",
        "global-fallback",
        "repo-wins-over-global",
        "integer-value",
        "none-is-noop",
    ],
)
def test_apply_config_to_env(
    monkeypatch: pytest.MonkeyPatch, env_var, repo_cfg, global_cfg, preset_env, expected
) -> None:
    if preset_env is None:
        monkeypatch.delenv(env_var, raising=False)
    else:
        monkeypatch.setenv(env_var, preset_env)
    kwargs = {"repo_cfg": repo_cfg}
    if global_cfg is not None:
        kwargs["global_cfg"] = global_cfg
    apply_config_to_env(**kwargs)
    if expected is None:
        assert env_var not in os.environ
    else:
        assert os.environ[env_var] == expected


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
    set_ambient_env({})
    try:
        assert commit_agent_cursor._resolve_agent_model(tmp_path) == "repo-model"
    finally:
        set_ambient_env(None)


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


def _write_repo_configs(paths_and_toml: dict[Path, str]) -> None:
    """Create each checkout dir (if needed) and its pyproject.toml."""
    for path, toml in paths_and_toml.items():
        path.mkdir(exist_ok=True)
        (path / "pyproject.toml").write_text(toml, encoding="utf-8")


def _fleet_bundles(
    repo_paths: tuple[Path, Path], ecosystem: str, manager: str
) -> tuple[list[Workspace], dict[Path, RepoBundle]]:
    """Build the two-checkout Workspace/RepoBundle scaffolding fleet tests patch around."""
    workspaces = [
        Workspace(
            path=path,
            ecosystem=ecosystem,
            manager=manager,
            git_repo=path,
            label=path.name,
        )
        for path in repo_paths
    ]
    bundles = {
        workspace.git_repo: RepoBundle(
            git_repo=workspace.git_repo,
            label=workspace.label,
            workspaces=[WorkspaceResult(workspace)],
        )
        for workspace in workspaces
    }
    return workspaces, bundles


def _patch_fleet_discovery(
    monkeypatch: pytest.MonkeyPatch,
    workspaces: list[Workspace],
    bundles: dict[Path, RepoBundle],
) -> None:
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
        run_mod.report, "print_json_report", lambda *args, **kwargs: None
    )


def test_fleet_run_resolves_config_per_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fleet runs resolve each bundle's own checkout config, not the launcher's
    — true both for a setting threaded through a scanner's kwargs
    (actions_lint_level) and one read via a resolver call inside
    process_workspace (timeout_check), the two shapes config resolution takes
    across the codebase."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _write_repo_configs(
        {
            launcher: '[tool.repo-ops]\nactions_lint_level = "launcher"\n'
            "timeout_check = 99\n",
            repo_a: '[tool.repo-ops]\nactions_lint_level = "block"\ntimeout_check = 11\n',
            repo_b: '[tool.repo-ops]\nactions_lint_level = "off"\ntimeout_check = 21\n',
        }
    )
    workspaces, bundles = _fleet_bundles((repo_a, repo_b), "actions", "actions")
    lint_captured: dict[Path, object] = {}
    timeout_captured: dict[Path, int] = {}

    monkeypatch.chdir(launcher)
    monkeypatch.delenv("REPO_OPS_ACTIONS_LINT_LEVEL", raising=False)
    monkeypatch.delenv("REPO_OPS_TIMEOUT_CHECK", raising=False)
    _patch_fleet_discovery(monkeypatch, workspaces, bundles)
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "merge_action_only_bundles",
        lambda grouped, roots, follow_symlinks=False: grouped,
    )

    def capture_timeout(workspace_result: WorkspaceResult, **kwargs: object):
        timeout_captured[workspace_result.workspace.git_repo] = resolve_timeout_check()
        return workspace_result

    monkeypatch.setattr(
        run_mod.orchestration,
        "process_workspace",
        capture_timeout,
    )

    def fake_scan(repo_path: Path, **kwargs: object) -> tuple[list, int]:
        lint_captured[repo_path] = kwargs["lint_level"]
        return [], 0

    monkeypatch.setattr(run_mod.actions, "scan_actions", fake_scan)

    rc = run_mod.run_upgrade(
        apply=False,
        argv=["--flat", "--json", str(repo_a), str(repo_b)],
    )

    assert rc == 0
    assert lint_captured == {repo_a: "block", repo_b: "off"}
    assert timeout_captured == {repo_a: 11, repo_b: 21}


def _bun_flags(workspace: Workspace) -> tuple[bool, ...]:
    return (
        bun_mod._bun_update_latest_enabled(workspace),
        bun_mod._bun_targeted_bump_enabled(workspace),
    )


def _deno_flags(workspace: Workspace) -> tuple[bool, ...]:
    return (
        _deno_update_latest_enabled(workspace),
        _deno_targeted_bump_enabled(workspace),
    )


def _yarn_flags(workspace: Workspace) -> tuple[bool, ...]:
    return (
        yarn_mod._yarn_update_latest_enabled(workspace),
        yarn_mod._yarn_targeted_bump_enabled(workspace),
    )


def _cross_ecosystem_sweep_flags(workspace: Workspace) -> tuple[bool, ...]:
    return (
        pnpm_mod._pnpm_update_latest_enabled(workspace),
        npm_mod._npm_targeted_bump_enabled(workspace),
        container_mod._container_flag_enabled(
            "container_update_latest", "REPO_OPS_CONTAINER_UPDATE_LATEST", workspace
        ),
        container_mod._container_flag_enabled(
            "container_refresh_sha", "REPO_OPS_CONTAINER_REFRESH_SHA", workspace
        ),
        precommit_mod._precommit_update_latest_enabled(workspace),
        precommit_mod._precommit_refresh_sha_enabled(workspace),
        submodules_mod._submodule_update_latest_enabled(workspace.git_repo),
        submodules_mod._submodule_refresh_sha_enabled(workspace.git_repo),
    )


_SWEEP_FLAG_CASES = [
    pytest.param(
        "node",
        "bun",
        ("bun_update_latest", "bun_targeted_bump"),
        ("REPO_OPS_BUN_UPDATE_LATEST", "REPO_OPS_BUN_TARGETED_BUMP"),
        _bun_flags,
        id="bun",
    ),
    pytest.param(
        "deno",
        "deno",
        ("deno_update_latest", "deno_targeted_bump"),
        ("REPO_OPS_DENO_UPDATE_LATEST", "REPO_OPS_DENO_TARGETED_BUMP"),
        _deno_flags,
        id="deno",
    ),
    pytest.param(
        "node",
        "yarn",
        ("yarn_update_latest", "yarn_targeted_bump"),
        ("REPO_OPS_YARN_UPDATE_LATEST", "REPO_OPS_YARN_TARGETED_BUMP"),
        _yarn_flags,
        id="yarn",
    ),
    pytest.param(
        "node",
        "pnpm",
        (
            "pnpm_update_latest",
            "npm_targeted_bump",
            "container_update_latest",
            "container_refresh_sha",
            "precommit_update_latest",
            "precommit_refresh_sha",
            "submodule_update_latest",
            "submodule_refresh_sha",
        ),
        (
            "REPO_OPS_PNPM_UPDATE_LATEST",
            "REPO_OPS_NPM_TARGETED_BUMP",
            "REPO_OPS_CONTAINER_UPDATE_LATEST",
            "REPO_OPS_CONTAINER_REFRESH_SHA",
            "REPO_OPS_PRECOMMIT_UPDATE_LATEST",
            "REPO_OPS_PRECOMMIT_REFRESH_SHA",
            "REPO_OPS_SUBMODULE_UPDATE_LATEST",
            "REPO_OPS_SUBMODULE_REFRESH_SHA",
        ),
        _cross_ecosystem_sweep_flags,
        id="cross-ecosystem-sweep",
    ),
]


@pytest.mark.parametrize(
    "ecosystem,manager,config_keys,env_vars,resolve", _SWEEP_FLAG_CASES
)
def test_default_sweep_and_conservative_override_per_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ecosystem: str,
    manager: str,
    config_keys: tuple[str, ...],
    env_vars: tuple[str, ...],
    resolve,
) -> None:
    """#136: the default sweep no longer force-writes these flags to "1" —
    each key's settings-table default is already "1", so per-checkout
    [tool.repo-ops] settings are genuinely respected (repo_a's explicit
    ``true``s and repo_b's explicit ``false``s both resolve as configured).
    ``--conservative`` still writes an explicit CLI-tier "0" for every key,
    which outranks per-checkout config unconditionally — both repos resolve
    to all-False under it regardless of their own config."""
    launcher = tmp_path / "launcher"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path in (launcher, repo_a, repo_b):
        path.mkdir()
    (launcher / "pyproject.toml").write_text(
        "[tool.repo-ops]\n" + "".join(f"{key} = false\n" for key in config_keys),
        encoding="utf-8",
    )
    (repo_a / "pyproject.toml").write_text(
        "[tool.repo-ops]\n" + "".join(f"{key} = true\n" for key in config_keys),
        encoding="utf-8",
    )
    (repo_b / "pyproject.toml").write_text(
        "[tool.repo-ops]\n" + "".join(f"{key} = false\n" for key in config_keys),
        encoding="utf-8",
    )

    workspaces = [
        Workspace(
            path=path,
            ecosystem=ecosystem,
            manager=manager,
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
    captured: dict[Path, tuple[bool, ...]] = {}

    monkeypatch.chdir(launcher)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for env_var in env_vars:
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
        captured[workspace_result.workspace.git_repo] = resolve(
            workspace_result.workspace
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

    # --no-only-outdated: the default filter drops the clean ("ok", no
    # changes) mock WorkspaceResults our capture_flags returns; since
    # group_by_repo hands back the same RepoBundle objects on both calls, an
    # in-place filter on the first call would silently starve the second.
    rc = run_mod.run_upgrade(
        apply=False,
        argv=[
            "--flat",
            "--json",
            "--skip-actions",
            "--no-only-outdated",
            str(repo_a),
            str(repo_b),
        ],
    )
    assert rc == 0
    assert captured == {
        repo_a: tuple(True for _ in config_keys),
        repo_b: tuple(False for _ in config_keys),
    }

    captured.clear()
    rc = run_mod.run_upgrade(
        apply=False,
        argv=[
            "--flat",
            "--json",
            "--skip-actions",
            "--no-only-outdated",
            "--conservative",
            str(repo_a),
            str(repo_b),
        ],
    )
    assert rc == 0
    assert captured == {
        repo_a: tuple(False for _ in config_keys),
        repo_b: tuple(False for _ in config_keys),
    }


def test_default_sweep_respects_checkout_config_over_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#136: the default sweep force-writes nothing, so normal CLI > env >
    repo config > global config > default layering applies — a per-checkout
    ``false`` wins over a global ``true`` (and over the settings-table
    default), exactly as it would for any other setting."""
    from repo_ops_core.config import ambient_env
    from repo_ops_js_pnpm import pnpm as pnpm_mod

    launcher = tmp_path / "launcher"
    repo = tmp_path / "repo"
    launcher.mkdir()
    repo.mkdir()
    (tmp_path / "config" / "repo-ops").mkdir(parents=True)
    (tmp_path / "config" / "repo-ops" / "config.toml").write_text(
        "pnpm_update_latest = true\n", encoding="utf-8"
    )
    (repo / "pyproject.toml").write_text(
        "[tool.repo-ops]\npnpm_update_latest = false\n", encoding="utf-8"
    )

    workspace = Workspace(
        path=repo,
        ecosystem="node",
        manager="pnpm",
        git_repo=repo,
        label="repo",
    )
    bundle = RepoBundle(
        git_repo=repo,
        label="repo",
        workspaces=[WorkspaceResult(workspace)],
    )
    captured: dict[str, object] = {}

    monkeypatch.chdir(launcher)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("REPO_OPS_PNPM_UPDATE_LATEST", raising=False)
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "discover_all",
        lambda *args, **kwargs: [workspace],
    )
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "group_by_repo",
        lambda found, roots: {repo: bundle},
    )

    def capture_resolution(workspace_result: WorkspaceResult, **kwargs: object):
        captured["resolved"] = pnpm_mod._pnpm_update_latest_enabled(workspace)
        captured["ambient_has_cli_key"] = "REPO_OPS_PNPM_UPDATE_LATEST" in (
            ambient_env() or {}
        )
        return workspace_result

    monkeypatch.setattr(
        run_mod.orchestration,
        "process_workspace",
        capture_resolution,
    )
    monkeypatch.setattr(
        run_mod.report, "print_json_report", lambda *args, **kwargs: None
    )

    rc = run_mod.run_upgrade(
        apply=False,
        argv=["--flat", "--json", "--skip-actions", str(repo)],
    )

    assert rc == 0
    assert captured == {"resolved": False, "ambient_has_cli_key": False}


def test_timeout_resolver_uses_global_config_outside_checkout_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery-time timeout resolution retains the global config layer."""
    config_dir = tmp_path / "config" / "repo-ops"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("timeout_check = 31\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("REPO_OPS_TIMEOUT_CHECK", raising=False)
    set_ambient_env({})
    try:
        assert resolve_timeout_check() == 31
    finally:
        set_ambient_env(None)


def test_node_verify_script_uses_ambient_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
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
        return make_proc()

    monkeypatch.setenv("REPO_OPS_NODE_VERIFY_SCRIPT", "injected")
    set_ambient_env({"REPO_OPS_NODE_VERIFY_SCRIPT": "caller"})
    monkeypatch.setattr(verify_mod, "run", fake_run)
    try:
        assert verify_mod.verify_node(workspace) is None
    finally:
        set_ambient_env(None)

    assert commands == [["npm", "ci"], ["npm", "run", "caller"]]


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
