"""Tests for the layered config module."""

from __future__ import annotations

import pytest

from repo_ops_core.config import (
    SETTINGS_TABLE,
    load_global_config,
    load_repo_config,
    resolve_setting,
)


def test_resolve_setting_cli_wins_over_env(monkeypatch):
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", "7")
    result = resolve_setting("timeout_check", cli_value="5")
    assert result == "5"


def test_resolve_setting_env_wins_over_default(monkeypatch):
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", "7")
    result = resolve_setting("timeout_check")
    assert result == "7"


def test_resolve_setting_global_config_wins_over_default(tmp_path):
    cfg = {"timeout_check": 9}
    result = resolve_setting("timeout_check", global_cfg=cfg)
    assert result == 9


def test_resolve_setting_repo_config_wins_over_global(tmp_path):
    global_cfg = {"timeout_check": 9}
    repo_cfg = {"timeout_check": 11}
    result = resolve_setting("timeout_check", global_cfg=global_cfg, repo_cfg=repo_cfg)
    assert result == 11


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


def test_settings_table_has_new_v014_entries() -> None:
    """New v0.14.0 settings are present in SETTINGS_TABLE."""
    names = [r[0] for r in SETTINGS_TABLE]
    for key in (
        "go_directive",
        "go_toolchain",
        "no_state",
        "no_lint_cache",
    ):
        assert key in names, f"Missing SETTINGS_TABLE entry: {key}"


# Every setting reachable from an env var: the SETTINGS_TABLE row maps the key
# to that env var, and resolve_setting reads it back.
ENV_SETTINGS = [
    ("go_directive", "REPO_OPS_GO_DIRECTIVE", "1.22"),
    ("go_toolchain", "REPO_OPS_GO_TOOLCHAIN", "go1.22.0"),
    ("no_state", "REPO_OPS_NO_STATE", "1"),
    ("no_lint_cache", "REPO_OPS_NO_LINT_CACHE", "1"),
]


@pytest.mark.parametrize(("key", "env_var", "value"), ENV_SETTINGS)
def test_resolve_setting_reads_env_var(monkeypatch, key: str, env_var: str, value: str) -> None:
    monkeypatch.setenv(env_var, value)
    assert resolve_setting(key) == value


@pytest.mark.parametrize(("key", "env_var", "value"), ENV_SETTINGS)
def test_settings_table_maps_key_to_env_var(key: str, env_var: str, value: str) -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == key)
    assert row[2] == env_var


# --- New env-gate settings ---
