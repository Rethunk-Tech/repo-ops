"""Tests for audit's config/env-driven --fail-on / --fail-on-unknown gates.

Precedence is CLI flag > env var (REPO_OPS_AUDIT_FAIL_ON /
REPO_OPS_AUDIT_FAIL_ON_UNKNOWN) > TOML config via XDG_CONFIG_HOME > default,
resolved via the shared settings table (repo_ops_core.config.resolve_setting).
Both settings are exercised identically since they share that resolver; only
the value shape (severity string vs boolean) differs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_audit.audit import VulnCount, WorkspaceResult, main


@pytest.fixture(autouse=True)
def _ignore_cwd_repo_config():
    with patch("repo_ops_audit.audit.load_repo_config", return_value={}):
        yield


def _results(**counts: int) -> list[WorkspaceResult]:
    return [
        WorkspaceResult(
            label="ws",
            path="/ws",
            ecosystem="go",
            manager="go",
            status="vulns",
            counts=VulnCount(**counts),
        )
    ]


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cli_args, counts: dict) -> int:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(**counts)),
    ):
        return main([*cli_args, str(tmp_path)])


# (env_var, env_value, cli_args, counts, expected_rc)
_ENV_CASES = [
    pytest.param(
        "REPO_OPS_AUDIT_FAIL_ON",
        "critical",
        (),
        {"high": 1},
        0,
        id="fail_on",
    ),
    pytest.param(
        "REPO_OPS_AUDIT_FAIL_ON_UNKNOWN",
        "1",
        ("--fail-on", "never"),
        {"unknown": 1},
        1,
        id="fail_on_unknown",
    ),
]

# (config_key, toml_value, cli_args, counts, expected_rc)
_CONFIG_CASES = [
    pytest.param(
        "audit_fail_on",
        '"critical"',
        (),
        {"high": 1},
        0,
        id="fail_on",
    ),
    pytest.param(
        "audit_fail_on_unknown",
        "true",
        ("--fail-on", "never"),
        {"unknown": 1},
        1,
        id="fail_on_unknown",
    ),
]

# (env_var, env_value, cli_args, counts) — CLI always wins, expected_rc is 1.
_CLI_OVERRIDES_ENV_CASES = [
    pytest.param(
        "REPO_OPS_AUDIT_FAIL_ON",
        "never",
        ("--fail-on", "high"),
        {"high": 1},
        id="fail_on",
    ),
    pytest.param(
        "REPO_OPS_AUDIT_FAIL_ON_UNKNOWN",
        "0",
        ("--fail-on", "never", "--fail-on-unknown"),
        {"unknown": 1},
        id="fail_on_unknown",
    ),
]

# (config_key, toml_value, cli_args, counts) — CLI always wins, expected_rc is 1.
_CLI_OVERRIDES_CONFIG_CASES = [
    pytest.param(
        "audit_fail_on",
        '"never"',
        ("--fail-on", "high"),
        {"high": 1},
        id="fail_on",
    ),
    pytest.param(
        "audit_fail_on_unknown",
        "false",
        ("--fail-on", "never", "--fail-on-unknown"),
        {"unknown": 1},
        id="fail_on_unknown",
    ),
]


@pytest.mark.parametrize(("env_var", "env_value", "cli_args", "counts", "expected_rc"), _ENV_CASES)
def test_env_applies_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_var: str,
    env_value: str,
    cli_args: tuple[str, ...],
    counts: dict,
    expected_rc: int,
) -> None:
    """An env var sets the gate when the matching CLI flag is not passed."""
    monkeypatch.setenv(env_var, env_value)
    assert _run(monkeypatch, tmp_path, cli_args, counts) == expected_rc


@pytest.mark.parametrize(
    ("config_key", "toml_value", "cli_args", "counts", "expected_rc"), _CONFIG_CASES
)
def test_config_file_applies_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_key: str,
    toml_value: str,
    cli_args: tuple[str, ...],
    counts: dict,
    expected_rc: int,
) -> None:
    """A [tool.repo-ops]/config.toml value sets the gate when no flag or env wins first."""
    config_dir = tmp_path / "repo-ops"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(f"{config_key} = {toml_value}\n", encoding="utf-8")
    assert _run(monkeypatch, tmp_path, cli_args, counts) == expected_rc


@pytest.mark.parametrize(("env_var", "env_value", "cli_args", "counts"), _CLI_OVERRIDES_ENV_CASES)
def test_cli_flag_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_var: str,
    env_value: str,
    cli_args: tuple[str, ...],
    counts: dict,
) -> None:
    """An explicit CLI flag wins over a lenient env-var value."""
    monkeypatch.setenv(env_var, env_value)
    assert _run(monkeypatch, tmp_path, cli_args, counts) == 1


@pytest.mark.parametrize(
    ("config_key", "toml_value", "cli_args", "counts"), _CLI_OVERRIDES_CONFIG_CASES
)
def test_cli_flag_overrides_config_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_key: str,
    toml_value: str,
    cli_args: tuple[str, ...],
    counts: dict,
) -> None:
    """An explicit CLI flag wins over a lenient config.toml value."""
    config_dir = tmp_path / "repo-ops"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(f"{config_key} = {toml_value}\n", encoding="utf-8")
    assert _run(monkeypatch, tmp_path, cli_args, counts) == 1
