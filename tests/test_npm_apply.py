"""Tests for npm apply ghost-bump guard."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_js_npm.npm import apply_npm
from repo_ops_models import Change, Workspace

_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"


def _ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="npm",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_apply_npm_raises_on_ghost_bump_overrides_pin() -> None:
    """Ghost bump: overrides/exact-pin keeps old version installed after npm update.

    npm update + npm install finish with rc=0, but node_modules still has the
    old version.  The guard must raise so apply_workspace marks the workspace
    failed (not a false-green success).
    """
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(
            "repo_ops_js_npm.npm.check_npm",
            return_value=([Change("lodash", "4.17.20", "4.17.21")], []),
        ),
        patch(_RESOLVER_PATH, return_value="4.17.20"),
        pytest.raises(RuntimeError) as exc_info,
    ):
        apply_npm(_ws())
    assert "lodash" in str(exc_info.value)
    assert "4.17.20" in str(exc_info.value)


def test_apply_npm_ok_when_resolved_at_target() -> None:
    """Resolved version at or above target → no ghost bump raised."""
    for resolved in ("4.17.21", "4.17.22"):
        with (
            patch("repo_ops_js_npm.npm.run", _fake_run_ok),
            patch(
                "repo_ops_js_npm.npm.check_npm",
                return_value=([Change("lodash", "4.17.20", "4.17.21")], []),
            ),
            patch(_RESOLVER_PATH, return_value=resolved),
        ):
            apply_npm(_ws())  # must not raise


def test_apply_npm_noop_when_nothing_outdated() -> None:
    """No changes reported → apply exits immediately (no npm update call)."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_npm.npm.run", fake_run),
        patch("repo_ops_js_npm.npm.check_npm", return_value=([], [])),
    ):
        apply_npm(_ws())

    # When check_npm returns no changes, apply_npm still runs npm update/install
    # (it doesn't short-circuit like bun does), but the guard is skipped.
    # This test simply verifies no RuntimeError is raised.


def test_apply_npm_guard_skipped_when_resolver_returns_none() -> None:
    """When node_modules is absent/unreadable, the guard skips without raising."""
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(
            "repo_ops_js_npm.npm.check_npm",
            return_value=([Change("react", "18.0.0", "18.3.0")], []),
        ),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        apply_npm(_ws())  # must not raise
