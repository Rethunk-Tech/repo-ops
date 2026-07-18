"""Regression tests for bun JSON / ANSI parsing (sub-task 8a).

Covers:
  * _changes_from_bun_text: ANSI escape codes stripped, (dev)/(peer)/(optional)
    suffixes removed, banner line and box borders skipped, literal package named
    "bun" accepted (i.e. NOT filtered by name).
  * _changes_from_bun_json: forward-compat JSON path.
  * audit bun path: JSON-first with text fallback when json.loads fails.

No subprocess or network calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import repo_ops_js_bun.bun as bun_mod
from repo_ops_audit.audit import _audit_node
from repo_ops_js_bun.bun import (
    _changes_from_bun_json,
    _changes_from_bun_text,
    _valid_bun_package_name,
    clear_bun_audit_version_overrides,
    set_bun_audit_version_overrides,
)

# ---------------------------------------------------------------------------
# Captured fixture: realistic bun outdated ANSI output
# ---------------------------------------------------------------------------

# │ is U+2502 (box drawings light vertical), used as the column separator.
_BOX_V = "│"

# Fixture string with ANSI colour codes, (dev) group suffixes, a literal
# package named "bun", and a banner that MUST be ignored.
_BUN_OUTDATED_ANSI = (
    "\x1b[1mbun outdated\x1b[0m v1.3.0 (abc12345)\n"
    "Resolving dependencies...\n"
    "\n"
    f"┌──────────"
    f"┬─────────"
    f"┬─────────"
    f"┬────────┐\n"
    f"{_BOX_V} Package        {_BOX_V} Current {_BOX_V} Update  {_BOX_V} Latest  {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # @types/bun (dev) — must strip (dev) suffix
    f"{_BOX_V} \x1b[32m@types/bun\x1b[0m \x1b[90m(dev)\x1b[0m {_BOX_V} 1.1.6   {_BOX_V} 1.1.10  {_BOX_V} 1.1.10  {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # react (no suffix)
    f"{_BOX_V} \x1b[32mreact\x1b[0m         {_BOX_V} 18.2.0  {_BOX_V} 18.3.0  {_BOX_V} 18.3.0  {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # typescript (dev) — must strip (dev) suffix
    f"{_BOX_V} \x1b[32mtypescript\x1b[0m \x1b[90m(dev)\x1b[0m {_BOX_V} 5.3.3   {_BOX_V} 5.6.2   {_BOX_V} 5.6.2   {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # literal package named "bun" — must NOT be filtered by name
    f"{_BOX_V} \x1b[32mbun\x1b[0m           {_BOX_V} 1.1.0   {_BOX_V} 1.2.0   {_BOX_V} 1.3.0   {_BOX_V}\n"
    f"└──────────"
    f"┴─────────"
    f"┴─────────"
    f"┴────────┘\n"
)


# ---------------------------------------------------------------------------
# _changes_from_bun_text — ANSI parser regression
# ---------------------------------------------------------------------------


def test_bun_text_parser_strips_ansi_and_parses_packages() -> None:
    changes, _behind = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    names = {c.name for c in changes}
    # All four packages should be detected
    assert "@types/bun" in names
    assert "react" in names
    assert "typescript" in names
    assert "bun" in names  # literal package named "bun" is valid


def test_bun_text_parser_strips_dev_suffix() -> None:
    changes, _ = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    names = {c.name for c in changes}
    # Must strip group suffix — no "(dev)" in any name
    assert not any("(dev)" in n for n in names)


def test_bun_text_parser_skips_banner_and_borders() -> None:
    """Banner line and box-border rows must not produce Change entries."""
    changes, _ = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    # "Package" header and box lines produce no Change
    assert all(c.name != "Package" for c in changes)
    # No entry whose from_ver is empty
    assert all(c.from_ver for c in changes)


def test_bun_text_parser_correct_versions() -> None:
    changes, _ = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    by_name = {c.name: c for c in changes}
    assert by_name["@types/bun"].from_ver == "1.1.6"
    assert by_name["@types/bun"].to == "1.1.10"
    assert by_name["react"].from_ver == "18.2.0"
    assert by_name["react"].to == "18.3.0"
    # "bun" update column (1.2.0) is the actionable target
    assert by_name["bun"].from_ver == "1.1.0"
    assert by_name["bun"].to == "1.2.0"


def test_bun_text_parser_behind_latest_when_update_eq_current() -> None:
    """Packages where Update == Current but Latest > Current go to behind_latest."""
    fixture = f"{_BOX_V} esbuild  {_BOX_V} 0.21.5  {_BOX_V} 0.21.5  {_BOX_V} 0.24.0  {_BOX_V}\n"
    changes, behind = _changes_from_bun_text(fixture)
    assert changes == []
    assert len(behind) == 1
    assert behind[0].name == "esbuild"
    assert behind[0].to == "0.24.0"


def test_bun_text_parser_peer_optional_suffix_stripped() -> None:
    """(peer) and (optional) group suffixes are stripped like (dev)."""
    fixture = (
        f"{_BOX_V} foo \x1b[90m(peer)\x1b[0m      {_BOX_V} 1.0.0  {_BOX_V} 2.0.0  {_BOX_V} 2.0.0  {_BOX_V}\n"
        f"{_BOX_V} bar \x1b[90m(optional)\x1b[0m  {_BOX_V} 1.0.0  {_BOX_V} 1.1.0  {_BOX_V} 1.1.0  {_BOX_V}\n"
    )
    changes, _ = _changes_from_bun_text(fixture)
    names = {c.name for c in changes}
    assert "foo" in names
    assert "bar" in names
    assert not any("(" in n for n in names)


def test_bun_text_parser_empty_output_no_changes() -> None:
    changes, behind = _changes_from_bun_text("")
    assert changes == []
    assert behind == []


# ---------------------------------------------------------------------------
# _valid_bun_package_name — edge cases
# ---------------------------------------------------------------------------


def test_valid_bun_package_name_literal_bun_accepted() -> None:
    """A package literally named 'bun' must not be rejected."""
    assert _valid_bun_package_name("bun") is True


def test_valid_bun_package_name_header_rejected() -> None:
    assert _valid_bun_package_name("Package") is False


def test_valid_bun_package_name_dash_prefix_rejected() -> None:
    assert _valid_bun_package_name("-invalid") is False


def test_valid_bun_package_name_scoped_accepted() -> None:
    assert _valid_bun_package_name("@types/node") is True


# ---------------------------------------------------------------------------
# bun audit: JSON-first path in _audit_node
# ---------------------------------------------------------------------------

_NPM_AUDIT_JSON_WITH_VULNS = json.dumps(
    {
        "auditReportVersion": 2,
        "vulnerabilities": {
            "lodash": {
                "name": "lodash",
                "severity": "high",
                "via": [
                    {
                        "source": 1469,
                        "name": "lodash",
                        "dependency": "lodash",
                        "title": "Prototype Pollution",
                        "url": "https://npmjs.com/advisories/1469",
                        "severity": "high",
                        "range": "<4.17.19",
                    }
                ],
            }
        },
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 1,
                "critical": 0,
                "total": 1,
            }
        },
    }
)


def test_audit_bun_uses_json_when_parseable() -> None:
    """When bun audit --json emits npm-advisory JSON, parse counts from JSON."""
    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, _NPM_AUDIT_JSON_WITH_VULNS, ""),
        ),
    ):
        result = _audit_node("/tmp/proj", "bun")

    assert result.status == "vulns"
    assert result.counts.high == 1
    assert result.counts.critical == 0


def test_audit_bun_falls_back_to_text_on_json_failure() -> None:
    """If stdout is not JSON (old bun without --json), use text parser."""
    text_output = "3 vulnerabilities (1 critical, 2 high)"
    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, text_output, ""),
        ),
    ):
        result = _audit_node("/tmp/proj", "bun")

    assert result.status == "vulns"
    assert result.counts.critical == 1
    assert result.counts.high == 2


def test_audit_bun_json_empty_stdout_falls_back_to_text_stderr() -> None:
    """Empty stdout → text parser sees combined stdout+stderr."""
    stderr_text = "1 vulnerability (1 moderate)"
    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, "", stderr_text),
        ),
    ):
        result = _audit_node("/tmp/proj", "bun")

    assert result.counts.moderate == 1


def test_audit_bun_zero_vulns_json() -> None:
    """JSON with zero counts + rc=0 → status ok."""
    clean_json = json.dumps(
        {
            "metadata": {
                "vulnerabilities": {"critical": 0, "high": 0, "moderate": 0, "low": 0}
            }
        }
    )
    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(0, clean_json, ""),
        ),
    ):
        result = _audit_node("/tmp/proj", "bun")

    assert result.status == "ok"
    assert result.counts.total == 0


# ---------------------------------------------------------------------------
# _changes_from_bun_json — consolidated from former test_bun_json_parser.py
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
