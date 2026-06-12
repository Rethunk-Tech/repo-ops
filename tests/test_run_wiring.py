"""Tests that run.py wires new flags correctly into orchestration."""

from __future__ import annotations

import os
from unittest.mock import patch


def test_jobs_default_resolves_to_cpu_count():
    """--jobs default None resolves to min(16, cpu_count or 4) in run_upgrade."""
    from repo_ops_upgrade.run import run_upgrade

    called_with_jobs: list[int] = []

    def _fake_body(**kwargs):
        called_with_jobs.append(kwargs["args"].jobs)
        return 0

    with patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body):
        run_upgrade(apply=False, argv=["/tmp"])

    assert len(called_with_jobs) == 1
    expected = min(16, os.cpu_count() or 4)
    assert called_with_jobs[0] == expected


def test_jobs_explicit_not_overridden():
    """--jobs 3 stays 3 (not overridden by cpu_count resolution)."""
    from repo_ops_upgrade.run import run_upgrade

    called_with_jobs: list[int] = []

    def _fake_body(**kwargs):
        called_with_jobs.append(kwargs["args"].jobs)
        return 0

    with patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body):
        run_upgrade(apply=False, argv=["/tmp", "--jobs", "3"])

    assert called_with_jobs[0] == 3


def test_actions_lint_level_flag_accepted():
    """--actions-lint-level is accepted by the upgrade parser."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--actions-lint-level", "warn"])
    assert args.actions_lint_level == "warn"
    args2 = parser.parse_args([])
    assert args2.actions_lint_level is None


def test_exclude_flag_accepted():
    """--exclude is accepted and accumulates into a list."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--exclude", "vendor", "--exclude", "node_modules"])
    assert args.exclude_patterns == ["vendor", "node_modules"]
    args2 = parser.parse_args([])
    assert args2.exclude_patterns is None
