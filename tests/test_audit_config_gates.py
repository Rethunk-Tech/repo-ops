"""Tests for audit's config/env-driven --fail-on / --fail-on-unknown gates.

Precedence is CLI flag > env var (REPO_OPS_AUDIT_FAIL_ON /
REPO_OPS_AUDIT_FAIL_ON_UNKNOWN) > default. ``main()`` also calls
apply_config_to_env, which promotes TOML config-file values into these same
env vars — but resolves against the real filesystem, which has no repo-ops
config files in the test sandbox, so these tests only exercise the env-var
layer (already sufficient to prove the precedence chain end to end).
"""

from __future__ import annotations

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
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
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


def test_fail_on_cli_flag_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """Explicit --fail-on-unknown wins even when the env var says '0' (off)."""
    monkeypatch.setenv("REPO_OPS_AUDIT_FAIL_ON_UNKNOWN", "0")
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.run_audit", return_value=_results(unknown=1)),
    ):
        rc = main(["--fail-on", "never", "--fail-on-unknown", str(tmp_path)])
    assert rc == 1
