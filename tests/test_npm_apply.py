"""Tests for npm apply ghost-bump guard."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_npm.npm import (
    apply_npm,
)
from repo_ops_models import Change

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


def test_apply_npm_noop_when_nothing_outdated(tmp_path: Path, make_workspace) -> None:
    """No changes reported → apply still runs npm update/install (no short-circuit)."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_npm.npm.run", fake_run),
        patch(_OUTDATED, return_value=([], [])),
    ):
        apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))

    # When outdated returns no changes, apply_npm still runs npm update/install
    # (it doesn't short-circuit like bun does), but the guard is skipped.
    assert any(c[:2] == ["npm", "update"] for c in calls)


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
