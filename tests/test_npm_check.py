"""Tests for npm check."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_core.holds import HoldRule
from repo_ops_js_npm.npm import check_npm
from repo_ops_models import Change


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


def test_latest_below_an_installed_prerelease_is_not_behind(tmp_path: Path, make_workspace) -> None:
    # ms publishes 3.0.0-beta.2 under `beta` while `latest` is 2.1.3.
    out = '{"ms": {"current": "3.0.0-beta.2", "wanted": "3.0.0-beta.2", "latest": "2.1.3"}}'
    proc = type("Proc", (), {"returncode": 1, "stdout": out, "stderr": ""})()
    with patch("repo_ops_js_npm.npm.run", return_value=proc):
        result = check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="d"))
    assert result == ([], [])


def _proc(*, returncode: int = 1, stdout: str = "{}", stderr: str = "") -> object:
    return type("Proc", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


def test_check_npm_outdated_nonzero_raises(tmp_path: Path, make_workspace) -> None:
    with (
        patch("repo_ops_js_npm.npm.run", return_value=_proc(returncode=2, stdout="", stderr="")),
        pytest.raises(RuntimeError, match="npm outdated failed"),
    ):
        check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="d"))


def test_check_npm_outdated_empty_stdout(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_js_npm.npm.run", return_value=_proc(returncode=0, stdout="  \n")):
        result = check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="d"))
    assert result == ([], [])


def test_check_npm_malformed_json_warns_and_returns_empty(
    capsys, tmp_path: Path, make_workspace
) -> None:
    with patch(
        "repo_ops_js_npm.npm.run",
        return_value=_proc(stdout="not valid json { at all }"),
    ):
        result = check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="d"))
    assert result == ([], [])
    err = capsys.readouterr().err
    assert "WARN" in err
    assert "not valid json" in err


def test_check_npm_parses_in_range_behind_and_skips_junk(tmp_path: Path, make_workspace) -> None:
    """Root outdated lists every member dep; only root directs are kept, and a
    name already in-range is not re-promoted from a second workspace entry."""
    (tmp_path / "package.json").write_text(
        '{"workspaces":["packages/*"],"dependencies":{"lodash":"^4.0.0","ms":"^1.0.0"}}'
    )
    member = tmp_path / "packages" / "app"
    member.mkdir(parents=True)
    (member / "package.json").write_text('{"name":"app","dependencies":{"left-pad":"^1.0.0"}}')

    out = (
        '{"lodash":['
        '{"current":"4.0.0","wanted":"4.17.21","latest":"4.17.21"},'
        '"not-a-dict",'
        '{"current":"4.0.0","wanted":"4.0.0","latest":"4.17.21"}'
        "],"
        '"ms":{"current":"1.0.0","wanted":"1.0.0","latest":"2.0.0"},'
        '"left-pad":{"current":"1.0.0","wanted":"1.3.0","latest":"1.3.0"}}'
    )

    def fake_run(cmd, cwd, timeout=None):
        stdout = out if Path(cwd) == tmp_path else "{}"
        return _proc(returncode=1, stdout=stdout)

    with patch("repo_ops_js_npm.npm.run", side_effect=fake_run):
        changes, behind = check_npm(
            make_workspace(tmp_path, ecosystem="node", manager="npm", label="d")
        )
    assert changes == [
        Change("lodash", "4.0.0", "4.17.21"),
        Change("ms", "1.0.0", "2.0.0"),
    ]
    assert behind == []


def test_check_npm_leaves_held_behind_unpromoted(tmp_path: Path, make_workspace) -> None:
    out = '{"lodash": {"current": "4.0.0", "wanted": "4.0.0", "latest": "4.17.21"}}'
    held = HoldRule(package="lodash", max_version="4.1")
    with (
        patch("repo_ops_js_npm.npm.run", return_value=_proc(stdout=out)),
        patch("repo_ops_js_npm.npm.resolve_package_hold", return_value=held),
    ):
        changes, behind = check_npm(
            make_workspace(tmp_path, ecosystem="node", manager="npm", label="d")
        )
    assert changes == []
    assert behind == [Change("lodash", "4.0.0", "4.17.21")]
