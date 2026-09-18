"""Tests for package version-ceiling ("hold") rules: repo_ops_core.holds."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
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
        # Go pseudo-version ceilings order by base version, then commit timestamp.
        ("v0.0.0-20260911000000-7970a1e00000", "0.0.0-20260821135717-be32def86098", False),
        ("v0.0.0-20260821135717-be32def86098", "v0.0.0-20260821135717-be32def86098", True),
        ("v0.0.0-20260101000000-aaaaaaaaaaaa", "v0.0.0-20260821135717-be32def86098", True),
        ("v1.2.4-0.20260101000000-aaaaaaaaaaaa", "v1.2.4-0.20260821135717-be32def86098", True),
        ("v1.2.4", "v1.2.4-0.20260821135717-be32def86098", False),
        ("v1.2.3", "v1.2.4-0.20260821135717-be32def86098", True),
        # Prerelease tags against plain ceilings keep prefix semantics.
        ("7.0.0-beta.1", "6", False),
        ("6.0.0-rc.1", "6", True),
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


# ---------------------------------------------------------------------------
# resolve_hold matching / precedence / expiry
# ---------------------------------------------------------------------------


def test_resolve_hold_no_match_returns_none() -> None:
    rules = [HoldRule(package="typescript", max_version="6")]
    assert (
        resolve_hold(
            rules,
            package="eslint",
            ecosystem="node",
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
        ecosystem="node",
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
            ecosystem="node",
            manager="npm",
            workspace_rel=".",
        )
        is None
    )
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="node",
        manager="pnpm",
        workspace_rel=".",
    )
    assert hit is not None


def test_resolve_hold_workspace_glob_scoping() -> None:
    rules = [HoldRule(package="typescript", max_version="7", workspace="packages/modern")]
    assert (
        resolve_hold(
            rules,
            package="typescript",
            ecosystem="node",
            manager="npm",
            workspace_rel="packages/legacy",
        )
        is None
    )
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="node",
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
            ecosystem="node",
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
        ecosystem="node",
        manager="npm",
        workspace_rel=".",
        today=date(2026, 1, 1),
    )
    assert hit is not None


def test_resolve_hold_expires_day_after_until() -> None:
    rules = [HoldRule(package="typescript", max_version="6", until="2026-01-01")]
    assert (
        resolve_hold(
            rules,
            package="typescript",
            ecosystem="node",
            manager="npm",
            workspace_rel=".",
            today=date(2026, 1, 2),
        )
        is None
    )


def test_resolve_hold_applies_on_until_date_inclusive() -> None:
    rules = [HoldRule(package="typescript", max_version="6", until="2026-01-01")]
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="node",
        manager="npm",
        workspace_rel=".",
        today=date(2026, 1, 1),
    )
    assert hit is not None


def test_resolve_hold_unparseable_until_treated_as_non_expiring() -> None:
    rules = [HoldRule(package="typescript", max_version="6", until="not-a-date")]
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="node",
        manager="npm",
        workspace_rel=".",
        today=date(2099, 1, 1),
    )
    assert hit is not None


def test_resolve_hold_unknown_ecosystem_filter_warns_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An ecosystem value that is neither a known ecosystem nor a known manager
    can never match anything — that must be loud, not silent, and deduped."""
    rules = [HoldRule(package="typescript", max_version="6", ecosystem="bogus-eco")]
    for _ in range(2):
        hit = resolve_hold(
            rules,
            package="typescript",
            ecosystem="node",
            manager="npm",
            workspace_rel=".",
        )
        assert hit is None
    err = capsys.readouterr().err
    assert err.count("bogus-eco") == 1
    assert "never apply" in err


def test_resolve_hold_ecosystem_js_alias_matches_node_workspace() -> None:
    """Docs (and the module docstring) instruct ``ecosystem = "js"`` — discovery
    never assigns that literal, only "node", so the alias must resolve or every
    such rule silently never matches."""
    rules = [
        HoldRule(
            package="typescript",
            max_version="6",
            until="2026-11-01",
            ecosystem="js",
            workspace="frontend",
            source="repo",
        )
    ]
    hit = resolve_hold(
        rules,
        package="typescript",
        ecosystem="node",
        manager="bun",
        workspace_rel="frontend",
        today=date(2026, 8, 13),
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
        ecosystem="node",
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
        ecosystem="node",
        manager="npm",
        workspace_rel=".",
    )
    assert hit is not None
    assert hit.max_version == "7"
    assert hit.source == "repo"
