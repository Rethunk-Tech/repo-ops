"""Go wiring for package version-ceiling ("hold") rules."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from repo_ops_core.holds import HoldRule
from repo_ops_go.go import (
    _changes_from_go_list_json,
    _enforce_version_ceilings,
    _explicit_go_get_targets,
    _filter_held_go_changes,
    apply_go,
)
from repo_ops_models import Workspace

_GLOBAL_HOLDS_CACHE = "repo_ops_go.go._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_go.go._repo_holds_cached"
_MODULE = "example.com/a"


def _ws(tmp_path) -> Workspace:
    return Workspace(
        path=tmp_path, ecosystem="go", manager="go", git_repo=tmp_path, label="mod"
    )


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _MODULE, "max_version": "1.5"}
    defaults.update(overrides)
    return HoldRule(**defaults)


@pytest.fixture(autouse=True)
def _no_repo_holds():
    with patch(_REPO_HOLDS_CACHE, return_value=()):
        yield


def _proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return type(
        "Proc", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr}
    )()


def _fake_run_with_gomod(list_stdout: str, require_paths: list[str]):
    gomod = json.dumps(
        {
            "Module": {"Path": "example.com/main"},
            "Require": [{"Path": p, "Version": "v0.0.0"} for p in require_paths],
        }
    )

    def fake_run(cmd, cwd, timeout=0):
        stdout = gomod if cmd[:3] == ["go", "mod", "edit"] else list_stdout
        return _proc(stdout)

    return fake_run


def test_changes_from_go_list_json_then_filter_held_past_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    line = json.dumps(
        {
            "Path": _MODULE,
            "Version": "v1.2.0",
            "Update": {"Path": _MODULE, "Version": "v2.0.0"},
        }
    )
    fake_run = _fake_run_with_gomod(line, [_MODULE])
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _changes_from_go_list_json(ws, patch_only=False)
        filtered = _filter_held_go_changes(ws, changes)
    assert len(changes) == 1
    assert filtered == []


def test_filter_held_go_changes_keeps_within_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    line = json.dumps(
        {
            "Path": _MODULE,
            "Version": "v1.2.0",
            "Update": {"Path": _MODULE, "Version": "v1.4.0"},
        }
    )
    fake_run = _fake_run_with_gomod(line, [_MODULE])
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _changes_from_go_list_json(ws, patch_only=False)
        filtered = _filter_held_go_changes(ws, changes)
    assert filtered == changes


def test_explicit_go_get_targets_skips_held_target_past_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    line = json.dumps(
        {
            "Path": _MODULE,
            "Version": "v1.2.0",
            "Update": {"Path": _MODULE, "Version": "v2.0.0"},
        }
    )
    fake_run = _fake_run_with_gomod(line, [_MODULE])
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        assert _explicit_go_get_targets(ws) == []


def test_enforce_version_ceilings_clamps_resolved_above_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    versions = {_MODULE: "v1.8.0"}

    def fake_run(cmd, cwd, timeout=0):
        if cmd[:3] == ["go", "mod", "edit"]:
            return _proc(
                json.dumps(
                    {
                        "Module": {"Path": "example.com/main"},
                        "Require": [{"Path": _MODULE, "Version": "v0.0.0"}],
                    }
                )
            )
        if cmd[:3] == ["go", "list", "-m"]:
            return _proc(json.dumps({"Path": _MODULE, "Version": versions[_MODULE]}))
        if cmd[:2] == ["go", "get"]:
            versions[_MODULE] = "v1.5.0"
            return _proc()
        if cmd == ["go", "mod", "tidy"]:
            return _proc()
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        _enforce_version_ceilings(ws)
    assert versions[_MODULE] == "v1.5.0"


def test_enforce_version_ceilings_raises_when_still_above_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        if cmd[:3] == ["go", "mod", "edit"]:
            return _proc(
                json.dumps(
                    {
                        "Module": {"Path": "example.com/main"},
                        "Require": [{"Path": _MODULE, "Version": "v0.0.0"}],
                    }
                )
            )
        if cmd[:3] == ["go", "list", "-m"]:
            return _proc(json.dumps({"Path": _MODULE, "Version": "v1.8.0"}))
        if cmd[:2] == ["go", "get"]:
            return _proc()  # succeeds but resolved version never actually moves
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_go.go.run", side_effect=fake_run),
        pytest.raises(RuntimeError, match="hold"),
    ):
        _enforce_version_ceilings(ws)


def test_apply_go_patch_mode_invokes_enforce_ceilings(tmp_path, monkeypatch) -> None:
    """apply_go's default (patch) mode calls the clamp-down safety net."""
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    ws = _ws(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0, env=None):
        calls.append(cmd)
        if cmd[:3] == ["go", "mod", "edit"]:
            return _proc(json.dumps({"Module": {"Path": "example.com/main"}}))
        return _proc()

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=()),
        patch("repo_ops_go.go.run", side_effect=fake_run),
        patch("repo_ops_go.go._run_with_go_work_retry", side_effect=fake_run),
    ):
        apply_go(ws)
    # No Require entries → _enforce_version_ceilings is a cheap no-op, but it
    # must have at least run go mod edit -json to discover that.
    assert any(cmd[:3] == ["go", "mod", "edit"] for cmd in calls)
