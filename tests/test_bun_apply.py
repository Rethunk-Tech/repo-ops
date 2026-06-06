"""Regression tests for bun apply (no ``--latest`` lockfile pollution)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_bun.bun import apply_bun
from repo_ops_models import Change, Workspace


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


def test_apply_bun_raises_on_ghost_bump_resolved_behind_target() -> None:
    """#112: bun.lock churns but an override/pin keeps the old version installed.

    The resolved version stays behind the requested target → fail loudly so the
    workspace is reported blocked, never a false-green successful upgrade.
    """

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.check_bun",
            return_value=([Change("zod", "3.25.76", "4.0.0")], []),
        ),
        patch(
            "repo_ops_js_bun.bun.resolved_version_from_node_modules",
            return_value="3.25.76",
        ),
    ):
        try:
            apply_bun(_ghost_ws())
        except RuntimeError as exc:
            assert "zod" in str(exc)
            assert "3.25.76" in str(exc)
        else:
            raise AssertionError("expected ghost-bump RuntimeError")


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
            patch(
                "repo_ops_js_bun.bun.resolved_version_from_node_modules",
                return_value=resolved,
            ),
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
