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

from repo_ops_audit.audit import _audit_node
from repo_ops_js_bun.bun import (
    _changes_from_bun_text,
    _valid_bun_package_name,
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
    fixture = (
        f"{_BOX_V} esbuild  {_BOX_V} 0.21.5  {_BOX_V} 0.21.5  {_BOX_V} 0.24.0  {_BOX_V}\n"
    )
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

_NPM_AUDIT_JSON_WITH_VULNS = json.dumps({
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
})


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
    clean_json = json.dumps({
        "metadata": {"vulnerabilities": {"critical": 0, "high": 0, "moderate": 0, "low": 0}}
    })
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
