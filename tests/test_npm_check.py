"""Tests for npm check."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_js_npm.npm import check_npm


def test_check_npm_passes_timeout_check(tmp_path: Path, make_workspace) -> None:
    """Npm outdated call must use TIMEOUT_CHECK (600s), not the default 300s."""
    captured: list[dict] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    with patch("repo_ops_js_npm.npm.run", side_effect=fake_run):
        check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))

    assert len(captured) == 1
    assert captured[0]["timeout"] == TIMEOUT_CHECK
