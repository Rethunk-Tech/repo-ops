"""Tests for repo_ops_upgrade.holds tag/SHA ceiling clamping."""

from __future__ import annotations

import pytest

from repo_ops_core.holds import HoldRule
from repo_ops_upgrade.holds import (
    matching_sha_tags,
    resolve_hold_sha_target,
    resolve_hold_tag_target,
)

_CURRENT = "aa" * 20
_HELD = "bb" * 20
_OLDER = "cc" * 20


def _rule(max_version: str) -> HoldRule:
    return HoldRule(package="example", max_version=max_version)


def test_resolve_hold_tag_target_ignores_nonsemver_current() -> None:
    assert (
        resolve_hold_tag_target("abc123", ["v1.0.0", "v1.4.0"], _rule("1.4"), context="submodule")
        is None
    )


@pytest.mark.parametrize(
    "current,tags,expected",
    [
        ("v2.0.0", ["not-a-tag", "1.4.0", "v1.3.0", "v1.4.0", "v1.5.0"], "v1.4.0"),
        ("2.0.0", ["not-a-tag", "v1.4.0", "1.3.0", "1.4.0", "1.5.0"], "1.4.0"),
    ],
)
def test_resolve_hold_tag_target_skips_mismatched_prefix_and_nonsemver_tags(
    current: str, tags: list[str], expected: str
) -> None:
    assert resolve_hold_tag_target(current, tags, _rule("1.4"), context="submodule") == expected


def test_resolve_hold_tag_target_skips_when_in_ceiling_tag_is_not_lower() -> None:
    # A release sits above a prerelease ceiling, but both share one numeric tuple.
    assert (
        resolve_hold_tag_target(
            "v1.2.3", ["v1.2.3-rc.1"], _rule("1.2.3-rc.1"), context="pre-commit"
        )
        is None
    )


def test_matching_sha_tags_skips_nonsemver_on_same_sha() -> None:
    got = matching_sha_tags(
        _CURRENT,
        [
            (_CURRENT, "not-a-tag"),
            (_CURRENT, "v1.2.3"),
            (_HELD, "v9.9.9"),
            (_CURRENT, "v1.2.4"),
        ],
        context="submodule",
    )
    assert got == [
        ((1, 2, 3), "v1.2.3", _CURRENT),
        ((1, 2, 4), "v1.2.4", _CURRENT),
    ]


def test_resolve_hold_sha_target_returns_none_without_matching_tags() -> None:
    assert (
        resolve_hold_sha_target(_CURRENT, [(_HELD, "v1.0.0")], _rule("1.4"), context="submodule")
        is None
    )


def test_resolve_hold_sha_target_clamps_to_highest_in_ceiling() -> None:
    assert resolve_hold_sha_target(
        _CURRENT,
        [
            (_CURRENT, "v2.0.0"),
            (_CURRENT, "not-a-tag"),
            (_HELD, "v1.4.0"),
            (_OLDER, "v1.3.0"),
            (_HELD, "1.4.0"),
            (_HELD, "v1.5.0"),
        ],
        _rule("1.4"),
        context="submodule",
    ) == ("v1.4.0", _HELD)


def test_resolve_hold_sha_target_returns_none_when_no_in_ceiling_candidate() -> None:
    assert (
        resolve_hold_sha_target(
            _CURRENT,
            [(_CURRENT, "v2.0.0"), (_HELD, "v1.5.0")],
            _rule("1.4"),
            context="submodule",
        )
        is None
    )


def test_resolve_hold_sha_target_returns_none_when_candidate_is_not_lower() -> None:
    assert (
        resolve_hold_sha_target(
            _CURRENT,
            [(_CURRENT, "v1.2.3"), (_HELD, "v1.2.3-rc.1")],
            _rule("1.2.3-rc.1"),
            context="pre-commit",
        )
        is None
    )
