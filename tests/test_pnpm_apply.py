"""Tests for pnpm apply ghost-bump guard and latest-mode omission fix."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_js_pnpm.pnpm import (
    _bucket_pnpm_pkg,
    apply_pnpm,
    clear_pnpm_audit_version_overrides,
    set_pnpm_audit_version_overrides,
)
from repo_ops_models import Change, Workspace

_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_pnpm.pnpm._pnpm_outdated"


def _ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="pnpm",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Task 2: ghost-bump guard wired into apply_pnpm
# ---------------------------------------------------------------------------


def test_apply_pnpm_raises_on_ghost_bump_overrides_pin() -> None:
    """Ghost bump: pnpm.overrides/resolutions keeps old version installed.

    pnpm update + pnpm install finish with rc=0, but node_modules still has
    the old version.  The guard must raise so the workspace is marked failed.
    """
    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([Change("axios", "1.6.0", "1.7.0")], [])),
        patch(_RESOLVER_PATH, return_value="1.6.0"),
        pytest.raises(RuntimeError) as exc_info,
    ):
        apply_pnpm(_ws())
    assert "axios" in str(exc_info.value)
    assert "1.6.0" in str(exc_info.value)


def test_apply_pnpm_ok_when_resolved_at_target() -> None:
    """Resolved version at or above target → no ghost bump raised."""
    for resolved in ("1.7.0", "1.8.0"):
        with (
            patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
            patch(_OUTDATED, return_value=([Change("axios", "1.6.0", "1.7.0")], [])),
            patch(_RESOLVER_PATH, return_value=resolved),
        ):
            apply_pnpm(_ws())  # must not raise


def test_apply_pnpm_guard_skipped_when_resolver_returns_none() -> None:
    """When node_modules is absent/unreadable, the guard skips without raising."""
    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([Change("vue", "3.0.0", "3.5.0")], [])),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        apply_pnpm(_ws())  # must not raise


def test_apply_pnpm_audit_override_absent_from_outdated() -> None:
    """#140: audit-safe pin must install even when outdated reports nothing."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    set_pnpm_audit_version_overrides({"pkg": "1.2.3"})
    try:
        with (
            patch("repo_ops_js_pnpm.pnpm.run", fake_run),
            patch(_OUTDATED, return_value=([], [])),
            patch(_RESOLVER_PATH, return_value="1.2.3"),
        ):
            apply_pnpm(_ws())
    finally:
        clear_pnpm_audit_version_overrides()

    add_cmds = [c for c in calls if c[:2] == ["pnpm", "add"] and "pkg@1.2.3" in c]
    assert add_cmds, f"expected pnpm add pkg@1.2.3, got {calls}"
    assert not any(c[:2] == ["pnpm", "update"] for c in calls)


# ---------------------------------------------------------------------------
# Task 5: _bucket_pnpm_pkg latest-mode omission fix
# ---------------------------------------------------------------------------


def test_bucket_pnpm_pkg_latest_mode_falls_through_when_latest_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#5: with REPO_OPS_PNPM_UPDATE_LATEST set, latest=='?' must fall through
    to the wanted check so a package with a known in-range bump is not omitted.

    Before the fix: the early ``return`` after the ``latest not in ('?','')``
    block swallowed packages where latest was '?' even when wanted > current,
    causing apply to silently skip an actionable in-range upgrade.
    """
    monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", "1")

    changes: list[Change] = []
    behind_latest: list[Change] = []

    # latest is '?' (unknown) but wanted > current — should land in changes.
    _bucket_pnpm_pkg(
        "axios",
        cur="1.6.0",
        wanted="1.7.0",
        latest="?",
        changes=changes,
        behind_latest=behind_latest,
    )

    assert len(changes) == 1, (
        "package with known wanted bump and unknown latest must land in changes"
    )
    assert changes[0].name == "axios"
    assert changes[0].to == "1.7.0"
    assert behind_latest == []


def test_bucket_pnpm_pkg_latest_mode_uses_latest_when_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With REPO_OPS_PNPM_UPDATE_LATEST set and a known latest, use latest."""
    monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", "1")

    changes: list[Change] = []
    behind_latest: list[Change] = []

    _bucket_pnpm_pkg(
        "axios",
        cur="1.6.0",
        wanted="1.7.0",
        latest="2.0.0",
        changes=changes,
        behind_latest=behind_latest,
    )

    assert len(changes) == 1
    assert changes[0].to == "2.0.0"
    assert behind_latest == []


def test_bucket_pnpm_pkg_normal_mode_unaffected_by_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without REPO_OPS_PNPM_UPDATE_LATEST, the fix must not alter behaviour."""
    monkeypatch.delenv("REPO_OPS_PNPM_UPDATE_LATEST", raising=False)

    changes: list[Change] = []
    behind_latest: list[Change] = []

    _bucket_pnpm_pkg(
        "axios",
        cur="1.6.0",
        wanted="1.7.0",
        latest="?",
        changes=changes,
        behind_latest=behind_latest,
    )

    # Normal mode: wanted > current → changes, regardless of latest.
    assert len(changes) == 1
    assert changes[0].to == "1.7.0"
