"""Coverage for bun.py's forward-compat JSON outdated parser and audit
override helpers.

``_changes_from_bun_json`` parses a future ``bun outdated --json`` payload;
today it is reachable only when bun emits JSON, so its branches (want-latest
on/off, missing/invalid meta, cur==target skip, the behind_latest bucket)
were largely untested. ``set_bun_audit_version_overrides`` /
``clear_bun_audit_version_overrides`` are the module-global channel used by
``repo_ops_audit.audit.apply_remediations``; this covers their round-trip.

No subprocess or network calls are made.
"""

from __future__ import annotations

import json

import repo_ops_js_bun.bun as bun_mod
from repo_ops_js_bun.bun import (
    _changes_from_bun_json,
    clear_bun_audit_version_overrides,
    set_bun_audit_version_overrides,
)

# ---------------------------------------------------------------------------
# _changes_from_bun_json — malformed input
# ---------------------------------------------------------------------------


def test_changes_from_bun_json_invalid_json_returns_empty() -> None:
    changes, behind = _changes_from_bun_json("not json")
    assert changes == []
    assert behind == []


def test_changes_from_bun_json_non_dict_top_level_returns_empty() -> None:
    changes, behind = _changes_from_bun_json(json.dumps([1, 2, 3]))
    assert changes == []
    assert behind == []


def test_changes_from_bun_json_skips_invalid_name_and_non_dict_meta() -> None:
    """Header-like/dash-prefixed names and non-dict meta values are skipped."""
    data = {
        "Package": {"current": "1.0.0", "update": "1.1.0", "latest": "1.1.0"},
        "-bad": {"current": "1.0.0", "update": "1.1.0", "latest": "1.1.0"},
        "not-a-dict": "oops",
    }
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert changes == []
    assert behind == []


def test_changes_from_bun_json_missing_update_target_skipped(monkeypatch) -> None:
    """No 'update'/'latest' field at all -> target stays '?' -> no entry either way."""
    monkeypatch.delenv("REPO_OPS_BUN_UPDATE_LATEST", raising=False)
    data = {"foo": {"current": "1.0.0"}}
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert changes == []
    assert behind == []


# ---------------------------------------------------------------------------
# _changes_from_bun_json — want_latest toggled via REPO_OPS_BUN_UPDATE_LATEST
# ---------------------------------------------------------------------------


def test_changes_from_bun_json_want_latest_off_targets_update_field(
    monkeypatch,
) -> None:
    monkeypatch.delenv("REPO_OPS_BUN_UPDATE_LATEST", raising=False)
    data = {"foo": {"current": "1.0.0", "update": "1.1.0", "latest": "2.0.0"}}
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert len(changes) == 1
    assert changes[0].name == "foo"
    assert changes[0].from_ver == "1.0.0"
    assert changes[0].to == "1.1.0"
    assert behind == []


def test_changes_from_bun_json_want_latest_on_targets_latest_field(
    monkeypatch,
) -> None:
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "1")
    data = {"foo": {"current": "1.0.0", "update": "1.1.0", "latest": "2.0.0"}}
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert len(changes) == 1
    assert changes[0].to == "2.0.0"
    assert behind == []


def test_changes_from_bun_json_falls_back_to_installed_key(monkeypatch) -> None:
    """'current' absent -> falls back to 'installed'."""
    monkeypatch.delenv("REPO_OPS_BUN_UPDATE_LATEST", raising=False)
    data = {"foo": {"installed": "1.0.0", "update": "1.1.0", "latest": "1.1.0"}}
    changes, _behind = _changes_from_bun_json(json.dumps(data))
    assert changes[0].from_ver == "1.0.0"


# ---------------------------------------------------------------------------
# _changes_from_bun_json — cur == target is a no-op; behind_latest bucket
# ---------------------------------------------------------------------------


def test_changes_from_bun_json_cur_equals_target_is_noop(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_BUN_UPDATE_LATEST", raising=False)
    data = {"foo": {"current": "1.0.0", "update": "1.0.0", "latest": "1.0.0"}}
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert changes == []
    assert behind == []


def test_changes_from_bun_json_behind_latest_when_update_stuck_at_current(
    monkeypatch,
) -> None:
    """update == current (in-range is exhausted) but latest is newer -> behind_latest."""
    monkeypatch.delenv("REPO_OPS_BUN_UPDATE_LATEST", raising=False)
    data = {"foo": {"current": "1.0.0", "update": "1.0.0", "latest": "2.0.0"}}
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert changes == []
    assert len(behind) == 1
    assert behind[0].name == "foo"
    assert behind[0].from_ver == "1.0.0"
    assert behind[0].to == "2.0.0"


def test_changes_from_bun_json_no_behind_latest_bucket_when_want_latest_on(
    monkeypatch,
) -> None:
    """With want_latest on, the behind_latest branch is unreachable (target IS latest)."""
    monkeypatch.setenv("REPO_OPS_BUN_UPDATE_LATEST", "1")
    data = {"foo": {"current": "1.0.0", "update": "1.0.0", "latest": "1.0.0"}}
    changes, behind = _changes_from_bun_json(json.dumps(data))
    assert changes == []
    assert behind == []


# ---------------------------------------------------------------------------
# set/clear_bun_audit_version_overrides — round trip
# ---------------------------------------------------------------------------


def test_bun_audit_version_overrides_round_trip() -> None:
    clear_bun_audit_version_overrides()
    assert bun_mod._AUDIT_VERSION_OVERRIDES == {}

    set_bun_audit_version_overrides({"pkg-a": "1.2.3", "pkg-b": "4.5.6"})
    assert bun_mod._AUDIT_VERSION_OVERRIDES == {"pkg-a": "1.2.3", "pkg-b": "4.5.6"}

    # Setting again replaces (not merges) the prior overrides.
    set_bun_audit_version_overrides({"pkg-c": "7.8.9"})
    assert bun_mod._AUDIT_VERSION_OVERRIDES == {"pkg-c": "7.8.9"}

    clear_bun_audit_version_overrides()
    assert bun_mod._AUDIT_VERSION_OVERRIDES == {}
