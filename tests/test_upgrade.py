"""Lightweight tests for upgrade ecosystem helpers and safety regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

# Import from the command module under test
from repo_ops.core.version_utils import normalize_version, semver_tuple
from repo_ops.upgrade.ecosystems.python import _parse_requirements_lines


def test_normalize_version_strips_prefixes():
    assert normalize_version("^1.2.3") == "1.2.3"
    assert normalize_version("~>2.0") == "2.0"
    assert normalize_version(">=1.0.0-beta") == "1.0.0"


def test_semver_tuple_parses():
    assert semver_tuple("1.2.3") == (1, 2, 3)
    assert semver_tuple("^4.5") == (4, 5)


def test_parse_requirements_lines_basic():
    text = "foo==1.0\nbar==2.0\n# comment\n-baz==3"
    assert _parse_requirements_lines(text) == {"foo": "1.0", "bar": "2.0"}


def test_checkers_use_monkeypatched_run(monkeypatch):
    """Safety regression: ensure checkers can be exercised with mocked pm calls."""
    from repo_ops.upgrade.ecosystems.node import check_bun
    from repo_ops.upgrade.models import Workspace

    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Package  current  latest\nfoo 1.0 latest 1.1\n", stderr=""
        )

    ws = Workspace(
        path=Path("/tmp"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp"),
        label="",
    )
    with patch("repo_ops.upgrade.ecosystems.node.run", fake_run):
        changes = check_bun(ws)
    assert len(changes) == 1
    assert changes[0].name == "foo"
    assert any("bun" in c[0] for c in calls)


def test_ecosystem_checker_and_applier_registry_keys():
    from repo_ops.upgrade.ecosystems import APPLIERS, CHECKERS

    expected = {
        "bun",
        "npm",
        "pnpm",
        "yarn",
        "go",
        "rust",
        "uv",
        "poetry",
        "pip-tools",
        "hatch",
    }
    assert expected == set(CHECKERS.keys())
    assert set(APPLIERS.keys()) == expected


def test_actions_discover_workflow_files_empty_without_github_dir(tmp_path):
    from repo_ops.upgrade.actions import discover_workflow_files

    assert discover_workflow_files(tmp_path) == []


def test_remediations_pm_exec_prefix_yarn():
    from repo_ops.upgrade.remediations import _pm_exec_prefix

    assert _pm_exec_prefix("yarn") == ["yarn"]
