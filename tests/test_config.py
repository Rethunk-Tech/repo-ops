"""Tests for the layered config module."""

from __future__ import annotations

import os

import pytest

from repo_ops_core.config import (
    SETTINGS_TABLE,
    apply_config_to_env,
    load_global_config,
    load_repo_config,
    resolve_setting,
)


def test_resolve_setting_cli_wins_over_env(monkeypatch):
    monkeypatch.setenv("REPO_OPS_GO_UPGRADE_MODE", "explicit")
    result = resolve_setting("go_upgrade_mode", cli_value="patch")
    assert result == "patch"


def test_resolve_setting_env_wins_over_default(monkeypatch):
    monkeypatch.setenv("REPO_OPS_GO_UPGRADE_MODE", "explicit")
    result = resolve_setting("go_upgrade_mode")
    assert result == "explicit"


def test_resolve_setting_default_when_no_override(monkeypatch):
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    result = resolve_setting("go_upgrade_mode")
    assert result is None  # go_upgrade_mode default is None in _SETTINGS_MAP


def test_resolve_setting_global_config_wins_over_default(tmp_path):
    cfg = {"go_upgrade_mode": "mvs"}
    result = resolve_setting("go_upgrade_mode", global_cfg=cfg)
    assert result == "mvs"


def test_resolve_setting_repo_config_wins_over_global(tmp_path):
    global_cfg = {"actions_lint_level": "warn"}
    repo_cfg = {"actions_lint_level": "off"}
    result = resolve_setting(
        "actions_lint_level", global_cfg=global_cfg, repo_cfg=repo_cfg
    )
    assert result == "off"


def test_resolve_setting_unknown_raises():
    with pytest.raises(KeyError, match="unknown setting"):
        resolve_setting("no_such_setting")


def test_load_repo_config_from_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.repo-ops]\njobs = 4\nexclude = ["vendor"]\n',
        encoding="utf-8",
    )
    cfg = load_repo_config(tmp_path)
    assert cfg.get("jobs") == 4
    assert cfg.get("exclude") == ["vendor"]


def test_load_repo_config_missing_file(tmp_path):
    assert load_repo_config(tmp_path) == {}


def test_load_repo_config_no_tool_repo_ops(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.other]\nkey = "val"\n', encoding="utf-8")
    assert load_repo_config(tmp_path) == {}


def test_load_global_config(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text("jobs = 12\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_global_config()
    assert result.get("jobs") == 12


def test_load_global_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_global_config() == {}


def test_settings_table_has_expected_columns():
    for row in SETTINGS_TABLE:
        assert len(row) == 5, f"expected 5 columns, got {len(row)} for {row}"


def test_settings_table_has_jobs_entry():
    names = [r[0] for r in SETTINGS_TABLE]
    assert "jobs" in names
    assert "actions_lint_level" in names


def test_settings_table_has_new_v014_entries() -> None:
    """New v0.14.0 settings are present in SETTINGS_TABLE."""
    names = [r[0] for r in SETTINGS_TABLE]
    for key in (
        "npm_targeted_bump",
        "pnpm_targeted_bump",
        "go_directive",
        "go_toolchain",
        "no_state",
        "no_lint_cache",
    ):
        assert key in names, f"Missing SETTINGS_TABLE entry: {key}"


def test_resolve_setting_precommit_refresh_sha_repo_config_wins_over_unset_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", raising=False)
    result = resolve_setting(
        "precommit_refresh_sha",
        repo_cfg={"precommit_refresh_sha": True},
    )
    assert result is True


# Every setting reachable from an env var: the SETTINGS_TABLE row maps the key
# to that env var, and resolve_setting reads it back.
ENV_SETTINGS = [
    ("npm_targeted_bump", "REPO_OPS_NPM_TARGETED_BUMP", "1"),
    ("pnpm_targeted_bump", "REPO_OPS_PNPM_TARGETED_BUMP", "1"),
    ("go_directive", "REPO_OPS_GO_DIRECTIVE", "1.22"),
    ("go_toolchain", "REPO_OPS_GO_TOOLCHAIN", "go1.22.0"),
    ("no_state", "REPO_OPS_NO_STATE", "1"),
    ("no_lint_cache", "REPO_OPS_NO_LINT_CACHE", "1"),
    ("actions_policy_level", "REPO_OPS_ACTIONS_POLICY_LEVEL", "warn"),
    ("precommit_refresh_sha", "REPO_OPS_PRECOMMIT_REFRESH_SHA", "1"),
    ("submodule_refresh_sha", "REPO_OPS_SUBMODULE_REFRESH_SHA", "1"),
    ("commit_agent_provider", "REPO_OPS_COMMIT_AGENT_PROVIDER", "cursor"),
    ("bun_update_latest", "REPO_OPS_BUN_UPDATE_LATEST", "1"),
    ("pnpm_update_latest", "REPO_OPS_PNPM_UPDATE_LATEST", "1"),
    ("yarn_update_latest", "REPO_OPS_YARN_UPDATE_LATEST", "1"),
    ("deno_update_latest", "REPO_OPS_DENO_UPDATE_LATEST", "1"),
    ("bun_targeted_bump", "REPO_OPS_BUN_TARGETED_BUMP", "1"),
    ("deno_targeted_bump", "REPO_OPS_DENO_TARGETED_BUMP", "1"),
    ("rust_targeted_bump", "REPO_OPS_RUST_TARGETED_BUMP", "1"),
    ("resume", "REPO_OPS_RESUME", "1"),
    ("submodule_update_latest", "REPO_OPS_SUBMODULE_UPDATE_LATEST", "1"),
    ("precommit_update_latest", "REPO_OPS_PRECOMMIT_UPDATE_LATEST", "1"),
    ("container_refresh_sha", "REPO_OPS_CONTAINER_REFRESH_SHA", "1"),
    ("hatch_migrate_uv_lock", "REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1"),
]


@pytest.mark.parametrize(("key", "env_var", "value"), ENV_SETTINGS)
def test_resolve_setting_reads_env_var(
    monkeypatch, key: str, env_var: str, value: str
) -> None:
    monkeypatch.setenv(env_var, value)
    assert resolve_setting(key) == value


@pytest.mark.parametrize(("key", "env_var", "value"), ENV_SETTINGS)
def test_settings_table_maps_key_to_env_var(key: str, env_var: str, value: str) -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == key)
    assert row[2] == env_var


# --- New env-gate settings (#138) ---


def test_settings_table_has_new_env_gate_entries() -> None:
    """Env-gate settings surfaced in v0.15+ are present in SETTINGS_TABLE."""
    names = [r[0] for r in SETTINGS_TABLE]
    for key in (
        "resume",
        "submodule_update_latest",
        "precommit_update_latest",
        "container_refresh_sha",
        "hatch_migrate_uv_lock",
        "audit_fail_on",
        "audit_fail_on_unknown",
    ):
        assert key in names, f"Missing SETTINGS_TABLE entry: {key}"


def test_settings_table_audit_fail_on_columns() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "audit_fail_on")
    assert row[1] == "--fail-on"
    assert row[2] == "REPO_OPS_AUDIT_FAIL_ON"
    assert row[3] == "audit_fail_on"
    assert row[4] == "high"


def test_settings_table_audit_fail_on_unknown_columns() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "audit_fail_on_unknown")
    assert row[1] == "--fail-on-unknown"
    assert row[2] == "REPO_OPS_AUDIT_FAIL_ON_UNKNOWN"
    assert row[3] == "audit_fail_on_unknown"
    assert row[4] == "0"


@pytest.mark.parametrize(
    ("toml_line", "env_var", "expected"),
    [
        ("resume = true", "REPO_OPS_RESUME", "1"),
        ('audit_fail_on = "critical"', "REPO_OPS_AUDIT_FAIL_ON", "critical"),
        ("audit_fail_on_unknown = true", "REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", "1"),
        ("hatch_migrate_uv_lock = true", "REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1"),
        ("container_refresh_sha = true", "REPO_OPS_CONTAINER_REFRESH_SHA", "1"),
        ("precommit_refresh_sha = true", "REPO_OPS_PRECOMMIT_REFRESH_SHA", "1"),
        ("submodule_refresh_sha = true", "REPO_OPS_SUBMODULE_REFRESH_SHA", "1"),
    ],
)
def test_setting_from_config_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    toml_line: str,
    env_var: str,
    expected: str,
) -> None:
    """A global config key lands in its env var via apply_config_to_env."""
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(f"{toml_line}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(env_var, raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get(env_var) == expected
    finally:
        # apply_config_to_env writes os.environ directly; monkeypatch.undo()
        # does not revert that, so pop it to avoid leaking into later tests.
        os.environ.pop(env_var, None)
