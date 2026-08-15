"""Tests for npm apply ghost-bump guard and audit overrides."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_js_npm.npm import (
    apply_npm,
    clear_npm_audit_version_overrides,
    set_npm_audit_version_overrides,
)
from repo_ops_models import Change

_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_npm.npm._npm_outdated"


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_apply_npm_raises_on_ghost_bump_overrides_pin(tmp_path: Path, make_workspace) -> None:
    """Ghost bump: overrides/exact-pin keeps old version installed after npm update.

    npm update + npm install finish with rc=0, but node_modules still has the
    old version.  The guard must raise so apply_workspace marks the workspace
    failed (not a false-green success).
    """
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([Change("lodash", "4.17.20", "4.17.21")], [])),
        patch(_RESOLVER_PATH, return_value="4.17.20"),
        pytest.raises(RuntimeError) as exc_info,
    ):
        apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))
    assert "lodash" in str(exc_info.value)
    assert "4.17.20" in str(exc_info.value)


def test_apply_npm_targeted_bump_defers_ghost_to_retry(tmp_path: Path, make_workspace) -> None:
    """Targeted bump mode sends a ghost target to the per-package retry."""
    change = Change("lodash", "4.17.20", "4.17.21")
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([change], [])),
        patch(_RESOLVER_PATH, return_value="4.17.20"),
        patch("repo_ops_js_npm.npm._npm_targeted_bump_enabled", return_value=True),
        patch("repo_ops_js_npm.npm.assert_targets_resolved") as guard,
        patch(
            "repo_ops_js_npm.npm.targeted_bump_behind",
            return_value=([], [change]),
        ) as retry,
    ):
        assert apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")) == [change]

    retry.assert_called_once()
    assert retry.call_args.args[1] == [change]
    guard.assert_not_called()


def test_apply_npm_ok_when_resolved_at_target(tmp_path: Path, make_workspace) -> None:
    """Resolved version at or above target → no ghost bump raised."""
    for resolved in ("4.17.21", "4.17.22"):
        with (
            patch("repo_ops_js_npm.npm.run", _fake_run_ok),
            patch(
                _OUTDATED, return_value=([Change("lodash", "4.17.20", "4.17.21")], [])
            ),
            patch(_RESOLVER_PATH, return_value=resolved),
        ):
            apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))  # must not raise


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
        apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))  # must not raise


def test_apply_npm_audit_override_absent_from_outdated(tmp_path: Path, make_workspace) -> None:
    """#140: audit-safe pin must install even when outdated reports nothing."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    set_npm_audit_version_overrides({"pkg": "1.2.3"})
    try:
        with (
            patch("repo_ops_js_npm.npm.run", fake_run),
            patch(_OUTDATED, return_value=([], [])),
            patch(_RESOLVER_PATH, return_value="1.2.3"),
        ):
            apply_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))
    finally:
        clear_npm_audit_version_overrides()

    install_cmds = [
        c for c in calls if c[:2] == ["npm", "install"] and "pkg@1.2.3" in c
    ]
    assert install_cmds, f"expected npm install pkg@1.2.3, got {calls}"
    # No bare npm update when only audit overrides were applied.
    assert not any(c == ["npm", "update"] for c in calls)
