"""Tests for the layered config module."""

from __future__ import annotations

import sys

import pytest
from repo_ops_core.config import (
    SETTINGS_TABLE,
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


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
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
