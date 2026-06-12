"""Tests for the repo-ops audit subcommand.

All subprocess calls are mocked — no network traffic, no real tool invocations.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from repo_ops_audit.audit import (
    VulnCount,
    WorkspaceResult,
    _audit_go,
    _audit_node,
    _audit_py_uv,
    _audit_rust,
    _cvss_to_score,
    _exit_code_for,
    _parse_bun_audit_text,
    _parse_npm_audit_json,
    _worse,
    build_audit_parser,
    main,
)

# ---------------------------------------------------------------------------
# VulnCount helpers
# ---------------------------------------------------------------------------


def test_vulncount_worst_returns_most_severe() -> None:
    v = VulnCount(critical=0, high=2, moderate=1, low=3)
    assert v.worst == "high"


def test_vulncount_worst_none_when_all_zero() -> None:
    assert VulnCount().worst is None


def test_vulncount_total() -> None:
    v = VulnCount(critical=1, high=2, moderate=3, low=4)
    assert v.total == 10


def test_vulncount_as_dict() -> None:
    v = VulnCount(critical=1, high=0, moderate=2, low=0)
    d = v.as_dict()
    assert d == {"critical": 1, "high": 0, "moderate": 2, "low": 0}


def test_worse_severity_ordering() -> None:
    assert _worse("critical", "high") == "critical"
    assert _worse("high", "critical") == "critical"
    assert _worse(None, "low") == "low"
    assert _worse("low", None) == "low"
    assert _worse(None, None) is None


# ---------------------------------------------------------------------------
# npm/pnpm/yarn audit JSON parsing
# ---------------------------------------------------------------------------


def test_parse_npm_audit_json_v7_metadata() -> None:
    data = {
        "metadata": {
            "vulnerabilities": {
                "critical": 1,
                "high": 3,
                "moderate": 5,
                "low": 2,
            }
        }
    }
    counts = _parse_npm_audit_json(data)
    assert counts.critical == 1
    assert counts.high == 3
    assert counts.moderate == 5
    assert counts.low == 2


def test_parse_npm_audit_json_v7_advisories_map() -> None:
    # Top-level "vulnerabilities" map with "via" entries
    data = {
        "vulnerabilities": {
            "lodash": {
                "via": [
                    {"url": "https://npmjs.com/advisories/1", "severity": "high"},
                    {"url": "https://npmjs.com/advisories/2", "severity": "critical"},
                ]
            },
            "express": {
                "via": [
                    {"url": "https://npmjs.com/advisories/3", "severity": "moderate"},
                ]
            },
        }
    }
    counts = _parse_npm_audit_json(data)
    assert counts.critical == 1
    assert counts.high == 1
    assert counts.moderate == 1


def test_parse_npm_audit_json_v6_advisories() -> None:
    data = {
        "advisories": {
            "1234": {"severity": "high"},
            "5678": {"severity": "low"},
        }
    }
    counts = _parse_npm_audit_json(data)
    assert counts.high == 1
    assert counts.low == 1


def test_parse_npm_audit_json_empty() -> None:
    counts = _parse_npm_audit_json({})
    assert counts.total == 0


# ---------------------------------------------------------------------------
# bun audit text parsing
# ---------------------------------------------------------------------------


def test_parse_bun_audit_text_extracts_counts() -> None:
    text = "3 vulnerabilities found\n1 critical, 2 high"
    counts = _parse_bun_audit_text(text)
    assert counts.critical == 1
    assert counts.high == 2


def test_parse_bun_audit_text_empty() -> None:
    counts = _parse_bun_audit_text("no vulnerabilities found")
    assert counts.total == 0


# ---------------------------------------------------------------------------
# CVSS score approximation
# ---------------------------------------------------------------------------


def test_cvss_to_score_high_impact() -> None:
    # C:H/I:H/A:H → all high impacts → score near 10
    score = _cvss_to_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert score >= 9.0


def test_cvss_to_score_no_impact() -> None:
    # C:N/I:N/A:N → no impact → score near 0
    score = _cvss_to_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N")
    assert score < 1.0


def test_cvss_to_score_missing_vector_returns_moderate() -> None:
    assert _cvss_to_score("") == 5.0


# ---------------------------------------------------------------------------
# go auditor (mocked subprocess)
# ---------------------------------------------------------------------------


def test_audit_go_skipped_when_govulncheck_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_go("/some/repo")
    assert result.status == "skipped"
    assert "govulncheck" in result.skip_reason


def test_audit_go_ok_no_findings() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(0, '{"config": {}}\n', ""),
        ),
    ):
        result = _audit_go("/some/repo")
    assert result.status == "ok"
    assert result.counts.total == 0


def test_audit_go_vulns_from_findings() -> None:
    finding_line = json.dumps({"finding": {"osv": "GO-2024-001", "severity": "high"}})
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, finding_line + "\n", ""),
        ),
    ):
        result = _audit_go("/some/repo")
    assert result.status == "vulns"
    assert result.counts.high >= 1


def test_audit_go_error_on_nonzero_no_parse() -> None:
    # Unparseable JSON + non-zero exit: fallback increments high count → vulns
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, "not json", "some error"),
        ),
    ):
        result = _audit_go("/some/repo")
    # The fallback path records 1 high-severity finding on parse failure
    assert result.status == "vulns"
    assert result.counts.high >= 1


# ---------------------------------------------------------------------------
# node auditor (mocked subprocess)
# ---------------------------------------------------------------------------


def test_audit_node_npm_skipped_when_npm_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "skipped"


def test_audit_node_npm_ok() -> None:
    npm_output = json.dumps(
        {
            "metadata": {
                "vulnerabilities": {"critical": 0, "high": 0, "moderate": 0, "low": 0}
            }
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/npm"),
        patch("repo_ops_audit.audit._run", return_value=(0, npm_output, "")),
    ):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "ok"


def test_audit_node_npm_vulns() -> None:
    npm_output = json.dumps(
        {
            "metadata": {
                "vulnerabilities": {"critical": 0, "high": 2, "moderate": 0, "low": 0}
            }
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/npm"),
        patch("repo_ops_audit.audit._run", return_value=(1, npm_output, "")),
    ):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "vulns"
    assert result.counts.high == 2


def test_audit_node_bun_text_parse() -> None:
    bun_output = "2 vulnerabilities (1 high, 1 moderate)"
    with (
        patch("shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._run", return_value=(1, bun_output, "")),
    ):
        result = _audit_node("/some/repo", "bun")
    assert result.status == "vulns"
    assert result.counts.high == 1
    assert result.counts.moderate == 1


# ---------------------------------------------------------------------------
# py-uv auditor (mocked subprocess)
# ---------------------------------------------------------------------------


def test_audit_py_uv_skipped_when_uv_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_py_uv("/some/repo")
    assert result.status == "skipped"


def test_audit_py_uv_skipped_when_pip_audit_not_importable() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, "", "No module named pip_audit"),
        ),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "skipped"
    assert "pip-audit" in result.skip_reason


def test_audit_py_uv_ok_no_vulns() -> None:
    pip_audit_out = json.dumps([{"name": "requests", "version": "2.31.0", "vulns": []}])
    # probe returns 0 (importable), audit returns 0 + clean output
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_audit.audit._run",
            side_effect=[(0, "", ""), (0, pip_audit_out, "")],
        ),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "ok"


def test_audit_py_uv_vulns() -> None:
    pip_audit_out = json.dumps(
        [
            {
                "name": "Pillow",
                "version": "9.0.0",
                "vulns": [
                    {
                        "id": "GHSA-001",
                        "aliases": ["CVE-2023-001"],
                        "fix_versions": ["9.1.0"],
                    }
                ],
            }
        ]
    )
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_audit.audit._run",
            side_effect=[(0, "", ""), (0, pip_audit_out, "")],
        ),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "vulns"
    assert result.counts.high == 1


# ---------------------------------------------------------------------------
# rust auditor (mocked subprocess)
# ---------------------------------------------------------------------------


def test_audit_rust_skipped_when_cargo_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_rust("/some/repo")
    assert result.status == "skipped"
    assert "cargo" in result.skip_reason


def test_audit_rust_skipped_when_cargo_audit_missing() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/cargo"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, "", "cargo: No such subcommand"),
        ),
    ):
        result = _audit_rust("/some/repo")
    assert result.status == "skipped"
    assert "cargo-audit" in result.skip_reason


def test_audit_rust_ok_no_vulns() -> None:
    cargo_json = json.dumps(
        {
            "vulnerabilities": {"list": [], "count": 0, "found": False},
            "warnings": {},
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/cargo"),
        patch(
            "repo_ops_audit.audit._run",
            side_effect=[(0, "cargo-audit 0.21.0", ""), (0, cargo_json, "")],
        ),
    ):
        result = _audit_rust("/some/repo")
    assert result.status == "ok"


def test_audit_rust_vulns() -> None:
    cargo_json = json.dumps(
        {
            "vulnerabilities": {
                "list": [
                    {
                        "advisory": {
                            "id": "RUSTSEC-2024-001",
                            "cvss": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        }
                    }
                ],
                "count": 1,
                "found": True,
            },
            "warnings": {},
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/cargo"),
        patch(
            "repo_ops_audit.audit._run",
            side_effect=[(0, "cargo-audit 0.21.0", ""), (0, cargo_json, "")],
        ),
    ):
        result = _audit_rust("/some/repo")
    assert result.status == "vulns"
    assert result.counts.critical == 1


# ---------------------------------------------------------------------------
# Exit code logic
# ---------------------------------------------------------------------------


def test_exit_code_never_is_always_zero() -> None:
    results = [
        WorkspaceResult(
            label="a",
            path="/a",
            ecosystem="go",
            manager="go",
            status="vulns",
            counts=VulnCount(critical=5),
        )
    ]
    assert _exit_code_for(results, "never") == 0


def test_exit_code_high_triggers_on_critical() -> None:
    results = [
        WorkspaceResult(
            label="a",
            path="/a",
            ecosystem="go",
            manager="go",
            status="vulns",
            counts=VulnCount(critical=1),
        )
    ]
    assert _exit_code_for(results, "high") == 1


def test_exit_code_critical_ignores_high() -> None:
    results = [
        WorkspaceResult(
            label="a",
            path="/a",
            ecosystem="go",
            manager="go",
            status="vulns",
            counts=VulnCount(high=5),
        )
    ]
    assert _exit_code_for(results, "critical") == 0


def test_exit_code_ok_results_are_always_zero() -> None:
    results = [
        WorkspaceResult(label="a", path="/a", ecosystem="go", manager="go", status="ok")
    ]
    assert _exit_code_for(results, "high") == 0


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_audit_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--fail-on" in out


def test_audit_json_output_empty_roots(tmp_path: pytest.TempPathFactory) -> None:
    """Empty roots → no workspaces → JSON with empty workspaces list, exit 0."""
    with patch("repo_ops_audit.audit.discover_repos", return_value={}):
        result = main(["--json", str(tmp_path)])
    assert result == 0


def test_audit_parser_fail_on_choices() -> None:
    parser = build_audit_parser()
    args = parser.parse_args(["--fail-on", "critical"])
    assert args.fail_on == "critical"
    args = parser.parse_args(["--fail-on", "never"])
    assert args.fail_on == "never"
    with pytest.raises(SystemExit):
        parser.parse_args(["--fail-on", "invalid"])
