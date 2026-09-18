"""Tests for pnpm apply ghost-bump guard and latest-mode omission fix."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_js_pnpm.pnpm import (
    _bucket_pnpm_pkg,
    apply_pnpm,
)
from repo_ops_models import Change

_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_pnpm.pnpm._pnpm_outdated"


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# ghost-bump guard wired into apply_pnpm
# ---------------------------------------------------------------------------


def test_apply_pnpm_targeted_bump_defers_ghost_to_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_workspace,
) -> None:
    """A blocked initial target is handed to targeted_bump_behind."""
    blocked = Change("axios", "1.6.0", "1.7.0")
    retry_inputs: list[list[Change]] = []

    def fake_targeted_bump(ws, behind, *, root=None):
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


# ---------------------------------------------------------------------------
# _bucket_pnpm_pkg latest-mode omission fix
# ---------------------------------------------------------------------------


def test_bucket_pnpm_pkg_latest_mode_falls_through_when_latest_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """latest=='?' must fall through
    to the wanted check so a package with a known in-range bump is not omitted.

    Before the fix: the early ``return`` after the ``latest not in ('?','')``
    block swallowed packages where latest was '?' even when wanted > current,
    causing apply to silently skip an actionable in-range upgrade.
    """

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
    """A known latest is the target."""

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
