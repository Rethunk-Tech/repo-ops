"""Tests for npm apply ghost-bump guard."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_core.constants import TIMEOUT_APPLY
from repo_ops_js_npm.npm import (
    apply_npm,
    run,
)
from repo_ops_models import Change


def test_timeout_apply_covers_install() -> None:
    assert TIMEOUT_APPLY >= 300


_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_npm.npm._npm_outdated"


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_apply_npm_ok_when_resolved_at_target(tmp_path: Path, make_workspace) -> None:
    """Resolved version at or above target → no ghost bump raised."""
    for resolved in ("4.17.21", "4.17.22"):
        with (
            patch("repo_ops_js_npm.npm.run", _fake_run_ok),
            patch(_OUTDATED, return_value=([Change("lodash", "4.17.20", "4.17.21")], [])),
            patch(_RESOLVER_PATH, return_value=resolved),
        ):
            apply_npm(
                make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
            )  # must not raise


def test_apply_npm_noop_when_nothing_outdated(
    tmp_path: Path, make_workspace, fake_run: Any
) -> None:
    """No changes reported → apply still runs npm update/install (no short-circuit)."""

    with (
        patch("repo_ops_js_npm.npm.run", fake_run),
        patch(_OUTDATED, return_value=([], [])),
    ):
        apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))

    # When outdated returns no changes, apply_npm still runs npm update/install
    # (it doesn't short-circuit like bun does), but the guard is skipped.
    assert any(c[:2] == ["npm", "update"] for c in fake_run.calls)


def test_apply_npm_guard_skipped_when_resolver_returns_none(tmp_path: Path, make_workspace) -> None:
    """When node_modules is absent/unreadable, the guard skips without raising."""
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([Change("react", "18.0.0", "18.3.0")], [])),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        apply_npm(
            make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
        )  # must not raise


def test_apply_npm_raises_when_update_fails(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=600, env=None):
        rc = 1 if cmd[:2] == ["npm", "update"] else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="update boom")

    with (
        patch("repo_ops_js_npm.npm.run", fake_run),
        patch(_OUTDATED, return_value=([], [])),
        pytest.raises(RuntimeError, match="update boom"),
    ):
        apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))


def test_apply_npm_raises_when_install_fails(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=600, env=None):
        rc = 1 if cmd[:2] == ["npm", "install"] else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

    with (
        patch("repo_ops_js_npm.npm.run", fake_run),
        patch(_OUTDATED, return_value=([], [])),
        pytest.raises(RuntimeError, match="npm install failed"),
    ):
        apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))


def test_run_rewrites_npm_install_through_corepack_and_merges_env(tmp_path: Path) -> None:
    captured: list[dict] = []

    def fake_process_run(cmd, cwd, timeout=300, env=None, stdin=None):
        captured.append({"cmd": cmd, "env": env, "stdin": stdin})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_js_npm.npm._process_run", side_effect=fake_process_run):
        run(["npm", "install", "lodash"], tmp_path, env={"FOO": "1"}, stdin="x")
        run(["npm", "outdated", "--json"], tmp_path)
        run(["node", "-e", "0"], tmp_path)

    assert captured[0]["cmd"] == [
        "corepack",
        "npm",
        "install",
        "lodash",
        "--ignore-scripts",
    ]
    assert captured[0]["env"]["FOO"] == "1"
    assert captured[0]["env"]["npm_config_ignore_scripts"] == "true"
    assert captured[0]["stdin"] == "x"
    assert captured[1]["cmd"] == ["corepack", "npm", "outdated", "--json"]
    assert captured[1]["env"] == {"npm_config_ignore_scripts": "true"}
    assert captured[2]["cmd"] == ["node", "-e", "0"]
    assert captured[2]["env"] is None
