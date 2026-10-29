"""Tests for Go upgrade mode alignment."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_go.go import (
    _changes_from_go_list_json,
    _explicit_go_get_targets,
    _raise_on_downgrade,
    _valid_go_get_target,
    apply_go,
)
from repo_ops_models import Workspace


def test_changes_from_go_list_json_pretty_printed(tmp_path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )
    modules = [
        {
            "Path": "example.com/a",
            "Version": "v1.2.3",
            "Update": {"Path": "example.com/a", "Version": "v1.2.4"},
        },
        {
            "Path": "example.com/b",
            "Version": "v2.0.0",
            "Update": {"Path": "example.com/b", "Version": "v2.1.0"},
        },
    ]
    stdout = "\n".join(json.dumps(m, indent="\t") for m in modules)
    fake_run = _fake_run_with_gomod(stdout, ["example.com/a", "example.com/b"])
    with patch("repo_ops_go.go.run", side_effect=fake_run):
        changes = _changes_from_go_list_json(ws)
    assert len(changes) == 2
    assert {c.name for c in changes} == {"example.com/a", "example.com/b"}


def _fake_run_with_gomod(list_stdout: str, require_paths: list[str]):
    """Dispatching fake for go.run: answers go list and go mod edit -json."""
    gomod = json.dumps(
        {
            "Module": {"Path": "example.com/main"},
            "Require": [{"Path": p, "Version": "v0.0.0"} for p in require_paths],
        }
    )

    def fake_run(cmd, cwd, timeout=0, env=None):
        stdout = gomod if cmd[:3] == ["go", "mod", "edit"] else list_stdout
        return type("Proc", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    return fake_run


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
    fake_run = _fake_run_with_gomod(line, ["example.com/foo"])
    with patch("repo_ops_go.go.run", side_effect=fake_run):
        assert _explicit_go_get_targets(ws) == ["example.com/foo@v1.0.1"]


def test_module_with_unloadable_metadata_is_skipped(tmp_path) -> None:
    ws = Workspace(path=tmp_path, ecosystem="go", manager="go", git_repo=tmp_path, label="mod")
    modules = [
        {"Path": "example.com/broken", "Version": "v0.5.1", "Error": {"Err": "404 Not Found"}},
        {
            "Path": "example.com/ok",
            "Version": "v1.0.0",
            "Update": {"Path": "example.com/ok", "Version": "v1.1.0"},
        },
    ]
    stdout = "\n".join(json.dumps(m) for m in modules)
    fake_run = _fake_run_with_gomod(stdout, ["example.com/broken", "example.com/ok"])
    with patch("repo_ops_go.go.run", side_effect=fake_run):
        assert _explicit_go_get_targets(ws) == ["example.com/ok@v1.1.0"]


def test_graph_only_modules_excluded(tmp_path) -> None:
    """Modules outside go.mod's Require list are ghost upgrades:
    go get bumps them in the graph, go mod tidy drops them, net diff zero.
    Neither check output nor explicit go get targets may include them."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )
    lines = "\n".join(
        json.dumps(m)
        for m in [
            {
                "Path": "example.com/recorded",
                "Version": "v1.0.0",
                "Update": {"Path": "example.com/recorded", "Version": "v1.0.1"},
            },
            {
                # In the module graph but absent from go.mod (e.g. stretchr/objx).
                "Path": "example.com/graph-only",
                "Version": "v0.5.2",
                "Update": {"Path": "example.com/graph-only", "Version": "v0.5.3"},
            },
        ]
    )
    fake_run = _fake_run_with_gomod(lines, ["example.com/recorded"])
    with patch("repo_ops_go.go.run", side_effect=fake_run):
        changes = _changes_from_go_list_json(ws)
        targets = _explicit_go_get_targets(ws)
    assert [c.name for c in changes] == ["example.com/recorded"]
    assert targets == ["example.com/recorded@v1.0.1"]


def test_unreadable_gomod_skips_filtering(tmp_path) -> None:
    """When go mod edit -json fails, fail open: report all updates."""
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

    def fake_run(cmd, cwd, timeout=0, env=None):
        if cmd[:3] == ["go", "mod", "edit"]:
            return type("Proc", (), {"returncode": 1, "stdout": "", "stderr": "boom"})()
        return type("Proc", (), {"returncode": 0, "stdout": line, "stderr": ""})()

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        changes = _changes_from_go_list_json(ws)
    assert [c.name for c in changes] == ["example.com/foo"]


def test_valid_go_get_target_rejects_flag_like_paths() -> None:
    assert _valid_go_get_target("example.com/foo@v1.0.1")
    assert not _valid_go_get_target("-evil@v1.0.0")
    assert not _valid_go_get_target("")


def test_explicit_go_get_targets_skips_flag_like_modules(tmp_path) -> None:
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
                "Path": "-evil",
                "Version": "v1.0.0",
                "Update": {"Path": "-evil", "Version": "v9.9.9"},
            }
        ),
        json.dumps(
            {
                "Path": "example.com/foo",
                "Version": "v1.0.0",
                "Update": {"Path": "example.com/foo", "Version": "v1.0.1"},
            }
        ),
    ]
    fake_run = _fake_run_with_gomod("\n".join(lines), ["-evil", "example.com/foo"])
    with patch("repo_ops_go.go.run", side_effect=fake_run):
        assert _explicit_go_get_targets(ws) == ["example.com/foo@v1.0.1"]


def test_apply_go_explicit_mode_uses_get_separator(tmp_path, fake_run: Any) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )

    with (
        patch("repo_ops_go.go.run", side_effect=fake_run),
        patch(
            "repo_ops_go.go._explicit_go_get_targets",
            return_value=["example.com/foo@v1.0.1"],
        ),
    ):
        apply_go(ws)
    assert ["go", "get", "--", "example.com/foo@v1.0.1"] in fake_run.calls
    assert ["go", "mod", "tidy"] in fake_run.calls


def test_raise_on_downgrade_flags_lowered_require_only() -> None:
    before = {"example.com/a": "v1.2.0", "example.com/b": "v0.0.0-20260917231906-eeb232e0883d"}
    _raise_on_downgrade(before, {"example.com/a": "v1.3.0", "example.com/c": "v1.0.0"})
    with pytest.raises(RuntimeError, match=r"example\.com/b"):
        _raise_on_downgrade(
            before,
            {"example.com/a": "v1.2.0", "example.com/b": "v0.0.0-20260901000000-aaaaaaaaaaaa"},
        )
