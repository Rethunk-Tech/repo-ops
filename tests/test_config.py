"""Tests for the layered config module."""

from __future__ import annotations

import os
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


def test_settings_table_npm_targeted_bump_env_var() -> None:
    """npm_targeted_bump maps to REPO_OPS_NPM_TARGETED_BUMP."""
    row = next(r for r in SETTINGS_TABLE if r[0] == "npm_targeted_bump")
    assert row[2] == "REPO_OPS_NPM_TARGETED_BUMP"


def test_settings_table_pnpm_targeted_bump_env_var() -> None:
    """pnpm_targeted_bump maps to REPO_OPS_PNPM_TARGETED_BUMP."""
    row = next(r for r in SETTINGS_TABLE if r[0] == "pnpm_targeted_bump")
    assert row[2] == "REPO_OPS_PNPM_TARGETED_BUMP"


def test_resolve_setting_npm_targeted_bump(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    result = resolve_setting("npm_targeted_bump")
    assert result == "1"


def test_resolve_setting_pnpm_targeted_bump(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    result = resolve_setting("pnpm_targeted_bump")
    assert result == "1"


def test_resolve_setting_go_directive(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_GO_DIRECTIVE", "1.22")
    result = resolve_setting("go_directive")
    assert result == "1.22"


def test_resolve_setting_go_toolchain(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_GO_TOOLCHAIN", "go1.22.0")
    result = resolve_setting("go_toolchain")
    assert result == "go1.22.0"


def test_resolve_setting_no_state(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    result = resolve_setting("no_state")
    assert result == "1"


def test_resolve_setting_no_lint_cache(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_NO_LINT_CACHE", "1")
    result = resolve_setting("no_lint_cache")
    assert result == "1"


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
    ):
        assert key in names, f"Missing SETTINGS_TABLE entry: {key}"


def test_settings_table_resume_env_var() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "resume")
    assert row[2] == "REPO_OPS_RESUME"


def test_settings_table_submodule_update_latest_env_var() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "submodule_update_latest")
    assert row[2] == "REPO_OPS_SUBMODULE_UPDATE_LATEST"


def test_settings_table_precommit_update_latest_env_var() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "precommit_update_latest")
    assert row[2] == "REPO_OPS_PRECOMMIT_UPDATE_LATEST"


def test_settings_table_container_refresh_sha_env_var() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "container_refresh_sha")
    assert row[2] == "REPO_OPS_CONTAINER_REFRESH_SHA"


def test_settings_table_hatch_migrate_uv_lock_env_var() -> None:
    row = next(r for r in SETTINGS_TABLE if r[0] == "hatch_migrate_uv_lock")
    assert row[2] == "REPO_OPS_HATCH_MIGRATE_UV_LOCK"


def test_resolve_setting_resume(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_RESUME", "1")
    result = resolve_setting("resume")
    assert result == "1"


def test_resolve_setting_submodule_update_latest(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_SUBMODULE_UPDATE_LATEST", "1")
    result = resolve_setting("submodule_update_latest")
    assert result == "1"


def test_resolve_setting_precommit_update_latest(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_UPDATE_LATEST", "1")
    result = resolve_setting("precommit_update_latest")
    assert result == "1"


def test_resolve_setting_container_refresh_sha(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "1")
    result = resolve_setting("container_refresh_sha")
    assert result == "1"


def test_resolve_setting_hatch_migrate_uv_lock(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    result = resolve_setting("hatch_migrate_uv_lock")
    assert result == "1"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_resume_from_config_file(tmp_path) -> None:
    """REPO_OPS_RESUME can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text("resume = true\n", encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_RESUME", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_RESUME") == "1"
    finally:
        # apply_config_to_env writes os.environ directly; monkeypatch.undo()
        # does not revert that, so pop it to avoid leaking into later tests.
        os.environ.pop("REPO_OPS_RESUME", None)
        monkeypatch.undo()

@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_audit_fail_on_from_config_file(tmp_path) -> None:
    """REPO_OPS_AUDIT_FAIL_ON can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        'audit_fail_on = "critical"\n', encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_AUDIT_FAIL_ON", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_AUDIT_FAIL_ON") == "critical"
    finally:
        os.environ.pop("REPO_OPS_AUDIT_FAIL_ON", None)
        monkeypatch.undo()

@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_audit_fail_on_unknown_from_config_file(tmp_path) -> None:
    """REPO_OPS_AUDIT_FAIL_ON_UNKNOWN can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "audit_fail_on_unknown = true\n", encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN") == "1"
    finally:
        os.environ.pop("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", None)
        monkeypatch.undo()

@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_hatch_migrate_uv_lock_from_config_file(tmp_path) -> None:
    """REPO_OPS_HATCH_MIGRATE_UV_LOCK can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "hatch_migrate_uv_lock = true\n", encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_HATCH_MIGRATE_UV_LOCK") == "1"
    finally:
        # apply_config_to_env writes os.environ directly; monkeypatch.undo()
        # does not revert that, so pop it to avoid leaking into later tests.
        os.environ.pop("REPO_OPS_HATCH_MIGRATE_UV_LOCK", None)
        monkeypatch.undo()


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_container_refresh_sha_from_config_file(tmp_path) -> None:
    """REPO_OPS_CONTAINER_REFRESH_SHA can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "container_refresh_sha = true\n", encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_CONTAINER_REFRESH_SHA", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_CONTAINER_REFRESH_SHA") == "1"
    finally:
        # apply_config_to_env writes os.environ directly; monkeypatch.undo()
        # does not revert that, so pop it to avoid leaking into later tests
        os.environ.pop("REPO_OPS_CONTAINER_REFRESH_SHA", None)
        monkeypatch.undo()


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_precommit_refresh_sha_from_config_file(tmp_path) -> None:
    """REPO_OPS_PRECOMMIT_REFRESH_SHA can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "precommit_refresh_sha = true\n", encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_PRECOMMIT_REFRESH_SHA") == "1"
    finally:
        # apply_config_to_env writes os.environ directly; monkeypatch.undo()
        # does not revert that, so pop it to avoid leaking into later tests.
        os.environ.pop("REPO_OPS_PRECOMMIT_REFRESH_SHA", None)
        monkeypatch.undo()


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
def test_submodule_refresh_sha_from_config_file(tmp_path) -> None:
    """REPO_OPS_SUBMODULE_REFRESH_SHA can be set via config file."""
    from repo_ops_core.config import apply_config_to_env

    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        "submodule_refresh_sha = true\n", encoding="utf-8"
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_SUBMODULE_REFRESH_SHA", raising=False)
    try:
        apply_config_to_env(global_cfg=load_global_config())
        assert os.environ.get("REPO_OPS_SUBMODULE_REFRESH_SHA") == "1"
    finally:
        # apply_config_to_env writes os.environ directly; monkeypatch.undo()
        # does not revert that, so pop it to avoid leaking into later tests.
        os.environ.pop("REPO_OPS_SUBMODULE_REFRESH_SHA", None)
        monkeypatch.undo()
