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
            return_value=[Change("foo", "1.0.0", "1.1.0")],
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
            return_value=[Change("-evil", "1.0.0", "9.9.9")],
        ),
    ):
        apply_bun(ws)

    assert calls == [["bun", "update"]]


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
        patch("repo_ops_js_bun.bun.check_bun", return_value=[]),
    ):
        apply_bun(ws)

    assert calls == []
