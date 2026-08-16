from __future__ import annotations

from pathlib import Path

from repo_ops_js.guards import unresolved_targets
from repo_ops_models import Change, Workspace


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )


def test_unresolved_targets_accepts_resolved_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "repo_ops_js.guards.resolved_version_from_node_modules",
        lambda _path, _name: "2.1.0",
    )
    change = Change("pkg", "1.0.0", "2.0.0")

    assert unresolved_targets(_workspace(tmp_path), [change]) == []


def test_unresolved_targets_reports_ghost_blocked_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "repo_ops_js.guards.resolved_version_from_node_modules",
        lambda _path, _name: "1.9.0",
    )
    change = Change("pkg", "1.0.0", "2.0.0")

    assert unresolved_targets(_workspace(tmp_path), [change]) == [(change, "1.9.0")]


def test_unresolved_targets_reports_installed_prerelease_before_stable_target(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "repo_ops_js.guards.resolved_version_from_node_modules",
        lambda _path, _name: "4.0.0-rc.1",
    )
    change = Change("pkg", "3.0.0", "4.0.0")

    assert unresolved_targets(_workspace(tmp_path), [change]) == [(change, "4.0.0-rc.1")]


def test_unresolved_targets_skips_empty_and_unknown_targets(tmp_path: Path, monkeypatch) -> None:
    def resolver(_path: Path, _name: str) -> str:
        raise AssertionError("skipped targets must not resolve node_modules")

    monkeypatch.setattr("repo_ops_js.guards.resolved_version_from_node_modules", resolver)

    requested = [
        Change("empty-target", "1.0.0", ""),
        Change("unknown-target", "1.0.0", "?"),
    ]

    assert unresolved_targets(_workspace(tmp_path), requested) == []
