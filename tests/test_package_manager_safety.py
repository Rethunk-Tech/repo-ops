"""Regression tests for packageManager parsing and PM version probes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js.workspaces import parse_package_manager, pm_version_argv
from repo_ops_models import Workspace
from repo_ops_upgrade_remediations_js.remediations import _pm_runtime_version


def test_parse_package_manager_rejects_path_like_names():
    assert parse_package_manager({"packageManager": "./evil@1.0.0"}) is None
    assert parse_package_manager({"packageManager": "npm/evil@1.0.0"}) is None
    assert parse_package_manager({"packageManager": "..\\evil@1.0.0"}) is None


def test_parse_package_manager_accepts_known_managers():
    assert parse_package_manager({"packageManager": "pnpm@9.0.0"}) == ("pnpm", "9.0.0")
    assert parse_package_manager({"packageManager": "NPM@10.1.0"}) == ("npm", "10.1.0")


def test_pm_version_argv_rejects_unknown_manager():
    assert pm_version_argv("evil") is None
    assert pm_version_argv("./npm") is None


def test_pm_runtime_version_uses_allowlisted_argv(tmp_path: Path):
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="",
    )
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="10.0.0\n", stderr="")

    with patch("repo_ops_upgrade_remediations_js.remediations.run", fake_run):
        assert _pm_runtime_version("npm", ws) == "10.0.0"
        assert _pm_runtime_version("./evil", ws) is None
    assert calls == [["npm", "--version"]]
