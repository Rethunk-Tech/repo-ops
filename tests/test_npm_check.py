"""Tests for npm check (#57 — check_npm must pass TIMEOUT_CHECK)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_js_npm.npm import check_npm
from repo_ops_models import Workspace
from repo_ops_upgrade.constants import TIMEOUT_CHECK


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )


def test_check_npm_passes_timeout_check(tmp_path: Path) -> None:
    """#57 — npm outdated call must use TIMEOUT_CHECK (600s), not the default 300s."""
    captured: list[dict] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    with patch("repo_ops_js_npm.npm.run", side_effect=fake_run):
        check_npm(_ws(tmp_path))

    assert len(captured) == 1
    assert captured[0]["timeout"] == TIMEOUT_CHECK
