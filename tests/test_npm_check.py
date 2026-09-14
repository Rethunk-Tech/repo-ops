"""Tests for npm check."""

from __future__ import annotations

import json
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


def test_check_npm_bucketing(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """In-range pkg → changes; out-of-range only → behind_latest."""
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "0")
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
        changes, behind_latest = check_npm(
            make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
        )

    assert len(changes) == 1
    assert changes[0].name == "lodash"
    assert changes[0].from_ver == "4.17.20"
    assert changes[0].to == "4.17.21"

    assert len(behind_latest) == 1
    assert behind_latest[0].name == "react"
    assert behind_latest[0].from_ver == "18.0.0"
    assert behind_latest[0].to == "19.0.0"


def test_check_npm_monorepo_checks_members_from_their_dirs(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Root output lists shared deps as a list and member deps it must not target itself."""
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "0")
    root_pkg = {"workspaces": ["apps/*"], "devDependencies": {"ms": "2.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(root_pkg))
    member = tmp_path / "apps" / "a"
    member.mkdir(parents=True)
    (member / "package.json").write_text(json.dumps({"dependencies": {"is-number": "6.0.0"}}))
    ms = {"current": "2.0.0", "wanted": "2.0.0", "latest": "2.1.3"}
    is_number = {"current": "6.0.0", "wanted": "6.0.0", "latest": "7.0.0"}
    outputs = {tmp_path: {"ms": [ms, ms], "is-number": is_number}, member: {"is-number": is_number}}
    cwds: list[Path] = []

    def fake_run(cmd, cwd, timeout=None):
        cwds.append(cwd)
        stdout = json.dumps(outputs[cwd])
        return type("Proc", (), {"returncode": 1, "stdout": stdout, "stderr": ""})()

    with patch("repo_ops_js_npm.npm.run", side_effect=fake_run):
        changes, behind = check_npm(make_workspace(tmp_path, manager="npm"))

    assert changes == []
    assert [(c.name, c.to) for c in behind] == [("ms", "2.1.3"), ("is-number", "7.0.0")]
    assert cwds == [tmp_path, member]
