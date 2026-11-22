"""GitHub Actions wiring for package version-ceiling ("hold") rules."""

from pathlib import Path

import pytest

from repo_ops_core.holds import HoldRule, clear_hold_caches
from repo_ops_upgrade.actions import (
    _matching_action_sha_tags,
    _resolve_hold_action_sha_target,
    _resolve_hold_action_tag_target,
)

_V4_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_V3_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_OTHER_SHA = "cccccccccccccccccccccccccccccccccccccccc"


@pytest.fixture(autouse=True)
def _isolated_hold_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the global hold config at a tmp dir and drop the process cache.

    ``_global_holds_cached``/``_repo_holds_cached`` are ``@cache``d for the
    process lifetime, so without this every test after the first would keep
    reusing whatever the first one loaded.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    clear_hold_caches()
    yield
    clear_hold_caches()


def _ceiling(max_version: str) -> HoldRule:
    return HoldRule(package="actions/checkout", max_version=max_version)


def test_hold_clamps_action_tag_to_highest_release_at_or_below_ceiling() -> None:
    tags = {"v4.2.0", "v3.9.1", "v3.8.0", "v2.0.0", "main"}
    rule = _ceiling("3")
    assert _resolve_hold_action_tag_target("v4.2.0", tags, rule) == "v3.9.1"
    assert _resolve_hold_action_tag_target("v3.1.0", tags, rule) is None
    assert _resolve_hold_action_tag_target("v4.2.0", {"v5.0.0", "v4.2.0"}, rule) is None
    assert _resolve_hold_action_tag_target("main", tags, rule) is None


def test_hold_clamps_sha_pin_to_highest_release_at_or_below_ceiling() -> None:
    info = {
        "sha_to_tags": {
            _V4_SHA: ["v4", "v4.2.0"],
            _V3_SHA: ["v3.9.1"],
            _OTHER_SHA: ["not-a-release"],
            "bbbbbbbbdddddddddddddddddddddddddddddddd": ["v3.0.0"],
        }
    }
    rule = _ceiling("3")
    assert _resolve_hold_action_sha_target("sha" + _V4_SHA, info, rule) == (
        _V3_SHA,
        "v4.2.0",
        "v3.9.1",
    )
    assert _resolve_hold_action_sha_target(_V3_SHA, info, rule) is None
    assert _resolve_hold_action_sha_target("deadbeef" * 5, info, rule) is None
    assert (
        _resolve_hold_action_sha_target(_V4_SHA, {"sha_to_tags": {_V4_SHA: ["v4.2.0"]}}, rule)
        is None
    )
    assert _matching_action_sha_tags(_V3_SHA[:12], info) == [((3, 9, 1), "v3.9.1", _V3_SHA)]
    assert _matching_action_sha_tags("bbbbbbbb", info) == []


def test_action_hold_clamp_skips_prereleases() -> None:
    rule = HoldRule(package="actions/cache", max_version="4")
    tags = {"v4", "v4.1.0", "v4.2.0-beta", "v5"}
    assert _resolve_hold_action_tag_target("v5", tags, rule) == "v4.1.0"
    info = {"sha_to_tags": {_V3_SHA: ["v4.1.0"], _V4_SHA: ["v4.2.0-rc.1"], _OTHER_SHA: ["v5.0.0"]}}
    assert _resolve_hold_action_sha_target(_OTHER_SHA, info, rule) == (_V3_SHA, "v5.0.0", "v4.1.0")
