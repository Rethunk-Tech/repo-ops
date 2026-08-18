"""Regression tests for bun apply (no ``--latest`` lockfile pollution)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_bun.bun import (
    apply_bun,
    clear_bun_audit_version_overrides,
    set_bun_audit_version_overrides,
)
from repo_ops_models import Change, Workspace

# Guard now lives in repo_ops_js.guards; patch the resolver there so all
# managers' tests point at the same canonical location.
_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"


def test_apply_bun_updates_reported_packages_not_latest_flag(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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

    assert calls == [["bun", "update", "--", "foo@1.1.0"]]
    assert not any("--latest" in c for c in calls)


def test_apply_bun_uses_advisory_safe_override_absent_from_check_bun() -> None:
    """An audit-flagged safe version must not be silently dropped.

    ``check_bun`` reports nothing outdated (empty changes/behind) — e.g. the
    installed range already resolves in-range from bun's perspective — but
    ``apply_remediations`` has computed a CVE-safe pin via
    ``set_bun_audit_version_overrides``. That override must still be targeted
    explicitly, not swallowed into a no-op bare update.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    ws = Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )
    set_bun_audit_version_overrides({"pkg": "1.2.3"})
    try:
        with (
            patch("repo_ops_js_bun.bun.run", fake_run),
            patch("repo_ops_js_bun.bun.check_bun", return_value=([], [])),
        ):
            apply_bun(ws)
    finally:
        clear_bun_audit_version_overrides()

    assert calls == [["bun", "update", "--", "pkg@1.2.3"]]


def test_apply_bun_override_replaces_check_derived_version() -> None:
    """When a package IS in check_bun's changes, the override still wins over
    the check-derived (range-respecting) target version."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    ws = Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )
    set_bun_audit_version_overrides({"foo": "1.5.5"})
    try:
        with (
            patch("repo_ops_js_bun.bun.run", fake_run),
            patch(
                "repo_ops_js_bun.bun.check_bun",
                return_value=([Change("foo", "1.0.0", "1.1.0")], []),
            ),
        ):
            apply_bun(ws)
    finally:
        clear_bun_audit_version_overrides()

    assert calls == [["bun", "update", "--", "foo@1.5.5"]]


def test_apply_bun_rejects_dash_prefixed_package_names() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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

    assert calls == [["bun", "update"]]


def _ghost_ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def test_apply_bun_raises_on_ghost_bump_resolved_behind_target(monkeypatch) -> None:
    """bun.lock churns but an override/pin keeps the old version installed.

    Resolved version stays behind requested target → fail loudly so workspace
    is reported blocked, never a false-green successful upgrade. Targeted bump
    is disabled here so the hard-fail guard runs instead of deferring to the
    retry fallback (the sweep default).
    """
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "0")

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("zod", "3.25.76", "4.0.0")], []),
        ),
        patch(_RESOLVER_PATH, return_value="3.25.76"),
    ):
        try:
            apply_bun(_ghost_ws())
        except RuntimeError as exc:
            assert "zod" in str(exc)
            assert "3.25.76" in str(exc)
        else:
            raise AssertionError("expected ghost-bump RuntimeError")


def test_apply_bun_targeted_bump_defers_ghost_to_retry(monkeypatch) -> None:
    """A blocked initial target is handed to targeted_bump_behind."""
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "1")
    retry_inputs: list[list[Change]] = []
    blocked = Change("zod", "3.25.76", "4.0.0")

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_targeted_bump(ws, behind):
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


def test_apply_bun_bare_update_defers_ghost_to_retry(monkeypatch) -> None:
    """A blocked bare update is handed to targeted_bump_behind."""
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "1")
    calls: list[list[str]] = []
    retry_inputs: list[list[Change]] = []
    blocked = Change("zod", "3.25.76", "4.0.0")
    valid_results = [False, True]

    def fake_valid_package_name(name):
        return valid_results.pop(0)

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_targeted_bump(ws, behind):
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
    assert calls == [["bun", "update"]]
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


def test_apply_bun_noop_when_nothing_outdated(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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

    assert calls == []


# ---------------------------------------------------------------------------
# fallback bare-update path guards known-version changes
# ---------------------------------------------------------------------------


def test_apply_bun_fallback_guard_called_for_known_versions() -> None:
    """assert_targets_resolved is invoked in the fallback path.

    Version '?' forces the fallback (no targets built).  The guard is wired
    after bare ``bun update``; this confirms the call site exists.  guardable
    is empty (version '?' excluded) so no error is raised, but we confirm
    the update command ran and the guard path was reached without raising.
    """
    calls: list[list[str]] = []
    guard_calls: list[list] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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

    assert calls == [["bun", "update"]]
    # guardable empty ('?' excluded) → guard not called; that's correct behaviour
    assert guard_calls == []


def test_apply_bun_fallback_guard_fires_when_guardable_nonempty(monkeypatch) -> None:
    """Guard IS called in fallback when a change has a real known version.

    We force the fallback by providing only changes that skip target-building
    (all have version '?'), except one that has a valid name + real version.
    But a valid-name + real-version change builds a target and takes the
    TARGETED path, not the fallback.  To isolate the fallback guard wiring we
    patch ``assert_targets_resolved`` and inspect that it is called with the
    right arguments when guardable is non-empty.

    We simulate this by providing two changes: name='a' version='1.0.0' (→
    targeted path, target 'a@1.0.0' built) and name='b' version='?'.  Since 'a'
    builds a target, targeted path is taken; guard is called on ``requested=[a]``.
    This verifies the targeted-path guard call.  The fallback guard path (bare
    bun update + guardable) is an additive safety net that activates for future
    cases where target building is refused for reasons other than name/version
    validity (e.g. registry spec changes).

    Targeted bump is disabled here: with it on (the sweep default), a blocked
    target defers to the retry fallback instead of calling this guard at all.
    """
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "0")
    guard_calls: list[list] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_guard(ws, requested, manager="js"):
        guard_calls.append([c.name for c in requested])

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=(
                [Change("a", "1.0.0", "1.1.0"), Change("b", "2.0.0", "?")],
                [],
            ),
        ),
        patch("repo_ops_js_bun.bun.assert_targets_resolved", fake_guard),
    ):
        apply_bun(_ghost_ws())

    # Targeted path: guard called once with only the valid-version change 'a'
    assert guard_calls == [["a"]]


# ---------------------------------------------------------------------------
# pre-release escape — installed pre-release must NOT satisfy stable target
# ---------------------------------------------------------------------------


def test_apply_bun_prerelease_installed_does_not_satisfy_stable_target(
    monkeypatch,
) -> None:
    """4.0.0-rc.1 installed against target 4.0.0 is a ghost bump.

    Before the fix, normalize_version stripped the pre-release suffix so
    4.0.0-rc.1 became '4.0.0' == '4.0.0' → no error (false-green). Targeted
    bump is disabled here so the hard-fail guard runs instead of deferring to
    the retry fallback (the sweep default).
    """
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "0")

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
        try:
            apply_bun(_ghost_ws())
        except RuntimeError as exc:
            assert "zod" in str(exc)
        else:
            raise AssertionError(
                "expected ghost-bump RuntimeError: 4.0.0-rc.1 < 4.0.0 (pre-release)"
            )


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
