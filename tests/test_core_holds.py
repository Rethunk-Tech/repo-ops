"""Tests for package version-ceiling ("hold") rules: repo_ops_core.holds."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.version_utils import version_satisfies_ceiling

# ---------------------------------------------------------------------------
# version_satisfies_ceiling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version,ceiling,expected",
    [
        ("6.5.0", "6", True),
        ("7.0.0", "6", False),
        ("5.9.9", "5.9", True),
        ("5.10.0", "5.9", False),
        ("4.0.0", "5.9", True),
        ("5.9.2", "5.9.2", True),
        ("5.9.3", "5.9.2", False),
        ("5.9.1", "5.9.2", True),
        ("5", "5.9.2", True),
    ],
)
def test_version_satisfies_ceiling(version: str, ceiling: str, expected: bool) -> None:
    assert version_satisfies_ceiling(version, ceiling) is expected


# ---------------------------------------------------------------------------
# config loading
# ---------------------------------------------------------------------------


def test_load_global_holds_parses_array_of_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        '[[hold]]\npackage = "typescript"\nmax_version = "6"\nuntil = "2026-09-01"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    rules = load_global_holds()
    assert len(rules) == 1
    assert rules[0].package == "typescript"
    assert rules[0].max_version == "6"
    assert rules[0].source == "global"


def test_load_global_holds_skips_malformed_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(
        '[[hold]]\npackage = "missing-max-version"\n'
        '[[hold]]\npackage = "typescript"\nmax_version = "6"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    rules = load_global_holds()
    assert len(rules) == 1
    assert rules[0].package == "typescript"


def test_load_global_holds_absent_config_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `[[hold]]` key at all (the common case) — no crash, empty list."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_global_holds() == []


def test_load_repo_holds_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.repo-ops.hold]]\npackage = "typescript"\nmax_version = "6"\n'
        'workspace = "packages/legacy-app"\n',
        encoding="utf-8",
    )
    rules = load_repo_holds(tmp_path)
    assert len(rules) == 1
    assert rules[0].workspace == "packages/legacy-app"
    assert rules[0].source == "repo"


# ---------------------------------------------------------------------------
# resolve_hold matching / precedence / expiry
# ---------------------------------------------------------------------------


def test_resolve_hold_no_match_returns_none() -> None:
    rules = [HoldRule(package="typescript", max_version="6")]
    assert (
        resolve_hold(
            rules,
            package="eslint",
            ecosystem="js",
            manager="npm",
            workspace_rel=".",
        )
        is None
    )


def test_resolve_hold_glob_package_match() -> None:
    rules = [HoldRule(package="@types/*", max_version="6")]
    hit = resolve_hold(
        rules,
        package="@types/node",
        ecosystem="js",
        manager="npm",
        workspace_rel=".",
    )
    assert hit is not None
    assert hit.max_version == "6"


def test_resolve_hold_ecosystem_filter() -> None:
    rules = [HoldRule(package="typescript", max_version="6", ecosystem="pnpm")]
    assert (
        resolve_hold(
            rules,
            package="typescript",
            ecosystem="js",
            manager="npm",
            workspace_rel=".",
        )
        is None
    )
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="js",
        manager="pnpm",
        workspace_rel=".",
    )
    assert hit is not None


def test_resolve_hold_workspace_glob_scoping() -> None:
    rules = [
        HoldRule(package="typescript", max_version="7", workspace="packages/modern")
    ]
    assert (
        resolve_hold(
            rules,
            package="typescript",
            ecosystem="js",
            manager="npm",
            workspace_rel="packages/legacy",
        )
        is None
    )
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="js",
        manager="npm",
        workspace_rel="packages/modern",
    )
    assert hit is not None


def test_resolve_hold_expired_rule_is_ignored() -> None:
    rules = [HoldRule(package="typescript", max_version="6", until="2020-01-01")]
    assert (
        resolve_hold(
            rules,
            package="typescript",
            ecosystem="js",
            manager="npm",
            workspace_rel=".",
            today=date(2026, 1, 1),
        )
        is None
    )


def test_resolve_hold_not_yet_expired() -> None:
    rules = [HoldRule(package="typescript", max_version="6", until="2099-01-01")]
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="js",
        manager="npm",
        workspace_rel=".",
        today=date(2026, 1, 1),
    )
    assert hit is not None


def test_resolve_hold_workspace_scoped_beats_repo_wide() -> None:
    rules = [
        HoldRule(package="typescript", max_version="6", source="global"),
        HoldRule(
            package="typescript",
            max_version="5",
            workspace="packages/legacy",
            source="global",
        ),
    ]
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="js",
        manager="npm",
        workspace_rel="packages/legacy",
    )
    assert hit is not None
    assert hit.max_version == "5"


def test_resolve_hold_repo_overrides_global_at_equal_specificity() -> None:
    rules = [
        HoldRule(package="typescript", max_version="6", source="global"),
        HoldRule(package="typescript", max_version="7", source="repo"),
    ]
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="js",
        manager="npm",
        workspace_rel=".",
    )
    assert hit is not None
    assert hit.max_version == "7"
    assert hit.source == "repo"
