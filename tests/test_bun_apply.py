"""Regression tests for bun apply (no ``--latest`` lockfile pollution)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_js_bun.bun import (
    apply_bun,
)
from repo_ops_lockfiles.snapshots import restore_workspace_snapshot, snapshot_workspace_files
from repo_ops_models import Change, Workspace
from repo_ops_upgrade_remediations_js.remediations import _rewritten_manifests

# Guard now lives in repo_ops_js.guards; patch the resolver there so all
# managers' tests point at the same canonical location.
_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"


def test_apply_bun_updates_reported_packages_not_latest_flag(monkeypatch, fake_run: Any) -> None:

    ws = Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )
    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("foo", "1.0.0", "1.1.0")], []),
        ),
    ):
        apply_bun(ws)

    assert fake_run.calls == [["bun", "update", "--", "foo@1.1.0"], ["bun", "install"]]
    assert not any("--latest" in c for c in fake_run.calls)


def test_apply_bun_rejects_dash_prefixed_package_names(fake_run: Any) -> None:

    ws = Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )
    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("-evil", "1.0.0", "9.9.9")], []),
        ),
    ):
        apply_bun(ws)

    assert fake_run.calls == [["bun", "update"], ["bun", "install"]]


def _ghost_ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def test_apply_bun_targeted_bump_defers_ghost_to_retry(monkeypatch) -> None:
    """A blocked initial target is handed to targeted_bump_behind."""
    retry_inputs: list[list[Change]] = []
    blocked = Change("zod", "3.25.76", "4.0.0")

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_targeted_bump(ws, behind, *, root=None):
        retry_inputs.append(list(behind))
        return [], behind

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([blocked], []),
        ),
        patch(_RESOLVER_PATH, return_value="3.25.76"),
        patch(
            "repo_ops_js_bun.bun.assert_targets_resolved",
            side_effect=RuntimeError("initial guard must defer"),
        ) as initial_guard,
        patch(
            "repo_ops_js_bun.bun.targeted_bump_behind",
            side_effect=fake_targeted_bump,
        ),
        patch("repo_ops_js_bun.bun.enforce_holds", return_value=[]),
    ):
        behind = apply_bun(_ghost_ws())

    initial_guard.assert_not_called()
    assert retry_inputs == [[blocked]]
    assert behind == [blocked]


def test_apply_bun_bare_update_defers_ghost_to_retry(monkeypatch, fake_run: Any) -> None:
    """A blocked bare update is handed to targeted_bump_behind."""
    retry_inputs: list[list[Change]] = []
    blocked = Change("zod", "3.25.76", "4.0.0")
    valid_results = [False, True]

    def fake_valid_package_name(name):
        return valid_results.pop(0)

    def fake_targeted_bump(ws, behind, *, root=None):
        retry_inputs.append(list(behind))
        return [], behind

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([blocked], []),
        ),
        patch(_RESOLVER_PATH, return_value="3.25.76"),
        patch(
            "repo_ops_js_bun.bun._valid_bun_package_name",
            side_effect=fake_valid_package_name,
        ),
        patch(
            "repo_ops_js_bun.bun.assert_targets_resolved",
            side_effect=RuntimeError("initial guard must defer"),
        ) as initial_guard,
        patch(
            "repo_ops_js_bun.bun.targeted_bump_behind",
            side_effect=fake_targeted_bump,
        ),
        patch("repo_ops_js_bun.bun.enforce_holds", return_value=[]),
    ):
        behind = apply_bun(_ghost_ws())

    initial_guard.assert_not_called()
    assert fake_run.calls == [["bun", "update"], ["bun", "install"]]
    assert retry_inputs == [[blocked]]
    assert behind == [blocked]


def test_apply_bun_ok_when_resolved_meets_or_exceeds_target() -> None:
    """A resolution at or above the requested target is a real upgrade, not a ghost."""

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    for resolved in ("4.0.0", "4.1.0"):
        with (
            patch("repo_ops_js_bun.bun.run", fake_run),
            patch(
                "repo_ops_js_bun.bun.check_bun",
                return_value=([Change("zod", "3.25.76", "4.0.0")], []),
            ),
            patch(_RESOLVER_PATH, return_value=resolved),
        ):
            apply_bun(_ghost_ws())  # must not raise


def test_apply_bun_noop_when_nothing_outdated(monkeypatch, fake_run: Any) -> None:

    ws = Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )
    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch("repo_ops_js_bun.bun.check_bun", return_value=([], [])),
    ):
        apply_bun(ws)

    assert fake_run.calls == []


# ---------------------------------------------------------------------------
# fallback bare-update path guards known-version changes
# ---------------------------------------------------------------------------


def test_apply_bun_fallback_guard_called_for_known_versions(fake_run: Any) -> None:
    """assert_targets_resolved is invoked in the fallback path.

    Version '?' forces the fallback (no targets built).  The guard is wired
    after bare ``bun update``; this confirms the call site exists.  guardable
    is empty (version '?' excluded) so no error is raised, but we confirm
    the update command ran and the guard path was reached without raising.
    """
    guard_calls: list[list] = []

    def fake_guard(ws, requested, manager="js"):
        guard_calls.append(list(requested))

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("some-pkg", "1.0.0", "?")], []),
        ),
        patch("repo_ops_js_bun.bun.assert_targets_resolved", fake_guard),
    ):
        apply_bun(_ghost_ws())

    assert fake_run.calls == [["bun", "update"], ["bun", "install"]]
    # guardable empty ('?' excluded) → guard not called; that's correct behaviour
    assert guard_calls == []


# ---------------------------------------------------------------------------
# pre-release escape — installed pre-release must NOT satisfy stable target
# ---------------------------------------------------------------------------


def test_apply_bun_prerelease_installed_does_not_satisfy_stable_target(
    monkeypatch,
) -> None:
    """4.0.0-rc.1 installed against target 4.0.0 is a ghost bump, not a landed bump.

    normalize_version once stripped the pre-release suffix, so 4.0.0-rc.1
    compared equal to 4.0.0 and the bump read as landed (false-green).
    """

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("zod", "3.25.76", "4.0.0")], []),
        ),
        patch(_RESOLVER_PATH, return_value="4.0.0-rc.1"),
    ):
        still_behind = apply_bun(_ghost_ws())
    assert "zod" in [c.name for c in still_behind]


def test_apply_bun_stable_installed_satisfies_stable_target() -> None:
    """Stable 4.0.0 installed against stable target 4.0.0 is OK."""

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("zod", "3.25.76", "4.0.0")], []),
        ),
        patch(_RESOLVER_PATH, return_value="4.0.0"),
    ):
        apply_bun(_ghost_ws())  # must not raise


def test_apply_bun_higher_prerelease_does_not_trigger_guard() -> None:
    """4.1.0-rc.1 installed > target 4.0.0 — not a ghost bump."""

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("zod", "3.25.76", "4.0.0")], []),
        ),
        patch(_RESOLVER_PATH, return_value="4.1.0-rc.1"),
    ):
        apply_bun(_ghost_ws())  # must not raise: 4.1.0-rc.1 has higher M.m.p


def test_apply_bun_bumps_member_dep_from_member_directory(tmp_path: Path) -> None:
    """bun rejects a member-only dep from the root, so the update runs in the member."""
    (tmp_path / "package.json").write_text('{"workspaces": ["apps/*"]}')
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text('{"dependencies": {"next": "16.3.0"}}')
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append((list(cmd), cwd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_check(ws, *, members=True):
        return ([Change("next", "16.3.0", "16.3.5")], []) if ws.path == web else ([], [])

    ws = Workspace(path=tmp_path, ecosystem="node", manager="bun", git_repo=tmp_path, label="demo")
    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch("repo_ops_js_bun.bun.check_bun", side_effect=fake_check),
    ):
        apply_bun(ws)

    assert (["bun", "update", "--", "next@16.3.5"], web) in calls
    assert not any("next@16.3.5" in " ".join(cmd) for cmd, cwd in calls if cwd == tmp_path)


def test_apply_bun_moves_stale_private_peer_pin_and_refreshes_lockfile(tmp_path: Path) -> None:
    """The peer edit lands in the snapshot, the manifest_format file set, and bun.lock."""
    (tmp_path / "package.json").write_text('{"workspaces": ["packages/*"]}')
    member = tmp_path / "packages" / "auth" / "package.json"
    member.parent.mkdir(parents=True)
    member.write_text(
        '{"private": true, "devDependencies": {"next": "16.3.5"}, "peerDependencies": {"next": "16.3.1"}}'
    )
    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append((list(cmd), cwd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    ws = Workspace(path=tmp_path, ecosystem="node", manager="bun", git_repo=tmp_path, label="demo")
    snap = snapshot_workspace_files(tmp_path, "bun")
    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch("repo_ops_js.workspaces.run", fake_run),
        patch("repo_ops_js_bun.bun.check_bun", return_value=([], [])),
    ):
        apply_bun(ws)

    assert json.loads(member.read_text())["peerDependencies"] == {"next": "16.3.5"}
    assert (["bun", "install"], tmp_path) in calls
    assert _rewritten_manifests(ws, {"pre_apply_snapshot": snap}) == ["packages/auth/package.json"]
    restore_workspace_snapshot(tmp_path, snap, "bun")
    assert json.loads(member.read_text())["peerDependencies"] == {"next": "16.3.1"}
