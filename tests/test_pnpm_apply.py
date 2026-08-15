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
from repo_ops_models import Change

_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_pnpm.pnpm._pnpm_outdated"


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Task 2: ghost-bump guard wired into apply_pnpm
# ---------------------------------------------------------------------------


def test_apply_pnpm_raises_on_ghost_bump_overrides_pin(
    tmp_path: Path, make_workspace, monkeypatch
) -> None:
    """Ghost bump: pnpm.overrides/resolutions keeps old version installed.

    pnpm update + pnpm install finish with rc=0, but node_modules still has
    the old version.  The guard must raise so the workspace is marked failed.
    Targeted bump is disabled here so the hard-fail guard runs instead of
    deferring to the retry fallback (the sweep default).
    """
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "0")
    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([Change("axios", "1.6.0", "1.7.0")], [])),
        patch(_RESOLVER_PATH, return_value="1.6.0"),
        pytest.raises(RuntimeError) as exc_info,
    ):
        apply_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )
    assert "axios" in str(exc_info.value)
    assert "1.6.0" in str(exc_info.value)


def test_apply_pnpm_targeted_bump_defers_ghost_to_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_workspace,
) -> None:
    """A blocked initial target is handed to targeted_bump_behind."""
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    blocked = Change("axios", "1.6.0", "1.7.0")
    retry_inputs: list[list[Change]] = []

    def fake_targeted_bump(ws, behind):
        retry_inputs.append(list(behind))
        return [], behind

    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([blocked], [])),
        patch(
            "repo_ops_js_pnpm.pnpm.unresolved_targets",
            return_value=[(blocked, "1.6.0")],
        ),
        patch(
            "repo_ops_js_pnpm.pnpm.assert_targets_resolved",
            side_effect=RuntimeError("initial guard must defer"),
        ) as initial_guard,
        patch(
            "repo_ops_js_pnpm.pnpm.targeted_bump_behind",
            side_effect=fake_targeted_bump,
        ),
        patch("repo_ops_js_pnpm.pnpm.enforce_holds", return_value=[]),
    ):
        behind = apply_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )

    initial_guard.assert_not_called()
    assert retry_inputs == [[blocked]]
    assert behind == [blocked]


def test_apply_pnpm_ok_when_resolved_at_target(tmp_path: Path, make_workspace) -> None:
    """Resolved version at or above target → no ghost bump raised."""
    for resolved in ("1.7.0", "1.8.0"):
        with (
            patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
            patch(_OUTDATED, return_value=([Change("axios", "1.6.0", "1.7.0")], [])),
            patch(_RESOLVER_PATH, return_value=resolved),
        ):
            apply_pnpm(
                make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
            )  # must not raise


def test_apply_pnpm_guard_skipped_when_resolver_returns_none(
    tmp_path: Path, make_workspace
) -> None:
    """When node_modules is absent/unreadable, the guard skips without raising."""
    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([Change("vue", "3.0.0", "3.5.0")], [])),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        apply_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )  # must not raise


def test_apply_pnpm_audit_override_absent_from_outdated(
    tmp_path: Path, make_workspace
) -> None:
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
            apply_pnpm(
                make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
            )
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
