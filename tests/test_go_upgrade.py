"""Tests for Go upgrade mode alignment (issue #7)."""

from __future__ import annotations

import json
from unittest.mock import patch

from repo_ops_go.go import (
    _changes_from_go_list_json,
    _explicit_go_get_targets,
    _is_patch_update,
    apply_go,
)
from repo_ops_models import Workspace


def test_is_patch_update_same_minor() -> None:
    assert _is_patch_update("v1.2.3", "v1.2.4")
    assert not _is_patch_update("v1.2.3", "v1.3.0")


def test_changes_from_go_list_json_patch_filter(tmp_path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )
    lines = [
        json.dumps(
            {
                "Path": "example.com/a",
                "Version": "v1.2.3",
                "Update": {"Path": "example.com/a", "Version": "v1.2.4"},
            }
        ),
        json.dumps(
            {
                "Path": "example.com/b",
                "Version": "v2.0.0",
                "Update": {"Path": "example.com/b", "Version": "v2.1.0"},
            }
        ),
    ]
    proc = type("Proc", (), {"returncode": 0, "stdout": "\n".join(lines), "stderr": ""})()
    with patch("repo_ops_go.go.run", return_value=proc):
        changes = _changes_from_go_list_json(ws, patch_only=True)
    assert len(changes) == 1
    assert changes[0].name == "example.com/a"


def test_explicit_go_get_targets(tmp_path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )
    line = json.dumps(
        {
            "Path": "example.com/foo",
            "Version": "v1.0.0",
            "Update": {"Path": "example.com/foo", "Version": "v1.0.1"},
        }
    )
    proc = type("Proc", (), {"returncode": 0, "stdout": line, "stderr": ""})()
    with patch("repo_ops_go.go.run", return_value=proc):
        assert _explicit_go_get_targets(ws) == ["example.com/foo@v1.0.1"]


def test_apply_go_patch_mode_runs_patch_all(tmp_path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls.append(cmd)
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with (
        patch.dict("os.environ", {"REPO_OPS_GO_UPGRADE_MODE": "patch"}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        apply_go(ws)
    assert ["go", "get", "-u=patch", "all"] in calls
    assert ["go", "mod", "tidy"] in calls
