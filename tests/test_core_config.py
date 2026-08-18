"""Core config helpers: timeouts, exclude_patterns, sidecar without tomllib."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_ops_core import config as config_mod
from repo_ops_core.config import (
    SETTINGS_TABLE,
    load_repo_config,
    resolve_setting,
)
from repo_ops_core.constants import (
    FIND_TIMEOUT,
    GIT_TIMEOUT,
    TIMEOUT_APPLY,
    TIMEOUT_CHECK,
    TIMEOUT_VERIFY,
    resolve_find_timeout,
    resolve_timeout_apply,
    resolve_timeout_check,
    resolve_timeout_verify,
)


def test_settings_table_timeout_defaults_match_constants() -> None:
    by_name = {row[0]: row[4] for row in SETTINGS_TABLE}
    assert by_name["timeout_check"] == str(TIMEOUT_CHECK)
    assert by_name["timeout_apply"] == str(TIMEOUT_APPLY)
    assert by_name["timeout_verify"] == str(TIMEOUT_VERIFY)
    assert by_name["find_timeout"] == str(FIND_TIMEOUT)
    assert by_name["git_timeout"] == str(GIT_TIMEOUT)
    assert TIMEOUT_CHECK == TIMEOUT_APPLY == TIMEOUT_VERIFY == FIND_TIMEOUT == 60


def test_settings_map_matches_settings_table() -> None:
    table_names = [row[0] for row in SETTINGS_TABLE]
    assert len(table_names) == len(set(table_names))
    assert set(config_mod._SETTINGS_MAP) == set(table_names)

    by_name = {row[0]: row for row in SETTINGS_TABLE}
    for name, (env_var, config_key, _default) in config_mod._SETTINGS_MAP.items():
        _setting, _flag, table_env_var, table_config_key, _table_default = by_name[name]
        assert env_var == (None if table_env_var == "(none)" else table_env_var)
        assert config_key == table_config_key


def test_resolve_setting_timeout_defaults_match_resolvers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "REPO_OPS_TIMEOUT_CHECK",
        "REPO_OPS_TIMEOUT_APPLY",
        "REPO_OPS_TIMEOUT_VERIFY",
        "REPO_OPS_FIND_TIMEOUT",
    ):
        monkeypatch.delenv(env, raising=False)

    assert resolve_setting("timeout_check") == TIMEOUT_CHECK
    assert resolve_setting("timeout_apply") == TIMEOUT_APPLY
    assert resolve_setting("timeout_verify") == TIMEOUT_VERIFY
    assert resolve_setting("find_timeout") == FIND_TIMEOUT
    assert resolve_setting("timeout_check") == resolve_timeout_check()
    assert resolve_setting("timeout_apply") == resolve_timeout_apply()
    assert resolve_setting("timeout_verify") == resolve_timeout_verify()
    assert int(resolve_setting("find_timeout")) == int(resolve_find_timeout())


def test_exclude_patterns_in_settings_map() -> None:
    assert "exclude_patterns" in config_mod._SETTINGS_MAP
    env_var, config_key, default = config_mod._SETTINGS_MAP["exclude_patterns"]
    assert env_var is None
    assert config_key == "exclude"
    assert default is None
    assert resolve_setting("exclude_patterns", repo_cfg={"exclude": ["vendor"]}) == ["vendor"]


def test_commit_agent_model_setting_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPO_OPS_COMMIT_AGENT_MODEL", raising=False)
    assert resolve_setting("commit_agent_model") == "composer-2.5"

    monkeypatch.setenv("REPO_OPS_COMMIT_AGENT_MODEL", "gpt-5.6-luna-xhigh")
    assert resolve_setting("commit_agent_model") == "gpt-5.6-luna-xhigh"

    row = next(row for row in SETTINGS_TABLE if row[0] == "commit_agent_model")
    assert row == (
        "commit_agent_model",
        "(none)",
        "REPO_OPS_COMMIT_AGENT_MODEL",
        "commit_agent_model",
        "composer-2.5",
    )


def test_load_repo_config_pyproject_wins_over_sidecar(tmp_path: Path) -> None:
    (tmp_path / ".repo-ops.toml").write_text("jobs = 3\nverbose = true\n", "utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.repo-ops]\njobs = 9\n", encoding="utf-8")
    # pyproject wins per-key; sidecar-only keys still apply.
    assert load_repo_config(tmp_path) == {"jobs": 9, "verbose": True}


def test_load_repo_config_reads_toml_with_shared_loader(tmp_path: Path) -> None:
    (tmp_path / ".repo-ops.toml").write_text("jobs = 3\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.repo-ops]\njobs = 9\n", encoding="utf-8")

    assert load_repo_config(tmp_path) == {"jobs": 9}


def test_container_cli_flags_removed_in_favor_of_conservative() -> None:
    """--container-update-latest/--container-refresh-sha are not argparse flags.

    Container sweep behaviour runs as part of the default full sweep in run.py;
    --conservative is the opt-out (env-var wiring covered by
    tests/test_run_targeted_bump_getattr.py).
    """
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--flat", "--container-update-latest", "/tmp"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--flat", "--container-refresh-sha", "/tmp"])
    assert parser.parse_args(["--conservative"]).conservative is True
