"""Tests for audit's config/env-driven --fail-on / --fail-on-unknown gates.

Precedence is CLI flag > env var (REPO_OPS_AUDIT_FAIL_ON /
REPO_OPS_AUDIT_FAIL_ON_UNKNOWN) > TOML config via XDG_CONFIG_HOME > default.
The tests cover both env and TOML config-file layers; CLI overrides remain
covered separately.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_audit.audit import VulnCount, WorkspaceResult, main


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


def test_fail_on_env_applies_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REPO_OPS_AUDIT_FAIL_ON=critical + no --fail-on flag → env sets the gate.

    A 'high' finding does not meet the 'critical' threshold, so exit stays 0 —
    proving the env var (not the "high" argparse default) drove the gate.
    """
    monkeypatch.setenv("REPO_OPS_AUDIT_FAIL_ON", "critical")
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(high=1)),
    ):
        rc = main([str(tmp_path)])
    assert rc == 0


def test_fail_on_from_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """audit_fail_on=critical in config.toml sets the gate when no flag is passed."""
    config_dir = tmp_path / "repo-ops"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'audit_fail_on = "critical"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("REPO_OPS_AUDIT_FAIL_ON", raising=False)
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(high=1)),
    ):
        rc = main([str(tmp_path)])
    assert rc == 0


def test_fail_on_cli_flag_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit --fail-on high wins over REPO_OPS_AUDIT_FAIL_ON=never in the env."""
    monkeypatch.setenv("REPO_OPS_AUDIT_FAIL_ON", "never")
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(high=1)),
    ):
        rc = main(["--fail-on", "high", str(tmp_path)])
    assert rc == 1


def test_fail_on_unknown_env_applies_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REPO_OPS_AUDIT_FAIL_ON_UNKNOWN=1 + no flag → unknown findings trigger exit 1."""
    monkeypatch.setenv("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", "1")
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(unknown=1)),
    ):
        rc = main(["--fail-on", "never", str(tmp_path)])
    assert rc == 1


def test_fail_on_unknown_cli_flag_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit --fail-on-unknown wins even when the env var says '0' (off)."""
    monkeypatch.setenv("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", "0")
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(unknown=1)),
    ):
        rc = main(["--fail-on", "never", "--fail-on-unknown", str(tmp_path)])
    assert rc == 1
