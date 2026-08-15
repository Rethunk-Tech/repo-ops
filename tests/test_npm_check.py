"""Tests for npm check (#57, #76)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_js_npm.npm import check_npm


def test_check_npm_passes_timeout_check(tmp_path: Path, make_workspace) -> None:
    """#57 — npm outdated call must use TIMEOUT_CHECK (600s), not the default 300s."""
    captured: list[dict] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()

    with patch("repo_ops_js_npm.npm.run", side_effect=fake_run):
        check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))

    assert len(captured) == 1
    assert captured[0]["timeout"] == TIMEOUT_CHECK


def test_check_npm_bucketing(tmp_path: Path, make_workspace) -> None:
    """#76 — in-range pkg → changes; out-of-range only → behind_latest."""
    # react: wanted == current (no in-range bump), latest > current → behind_latest.
    # lodash: wanted > current (in-range bump) → changes.
    payload = {
        "react": {"current": "18.0.0", "wanted": "18.0.0", "latest": "19.0.0"},
        "lodash": {"current": "4.17.20", "wanted": "4.17.21", "latest": "4.17.21"},
    }

    def fake_run(cmd, cwd, timeout=None):
        return type(
            "Proc",
            (),
            {"returncode": 1, "stdout": json.dumps(payload), "stderr": ""},
        )()

    with patch("repo_ops_js_npm.npm.run", side_effect=fake_run):
        changes, behind_latest = check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))

    assert len(changes) == 1
    assert changes[0].name == "lodash"
    assert changes[0].from_ver == "4.17.20"
    assert changes[0].to == "4.17.21"

    assert len(behind_latest) == 1
    assert behind_latest[0].name == "react"
    assert behind_latest[0].from_ver == "18.0.0"
    assert behind_latest[0].to == "19.0.0"
