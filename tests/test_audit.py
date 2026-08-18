"""Tests for the repo-ops audit subcommand.

All subprocess calls are mocked — no network traffic, no real tool invocations.
"""

from __future__ import annotations

import json
from pathlib import Path
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
    _probe_bun_audit,
    _probe_pip_audit,
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


def test_vulncount_total_with_unknown() -> None:
    v = VulnCount(critical=1, high=2, moderate=3, low=4, unknown=5)
    assert v.total == 10
    assert v.total_with_unknown == 15


def test_vulncount_unknown_not_in_total() -> None:
    v = VulnCount(unknown=3)
    assert v.total == 0
    assert v.total_with_unknown == 3
    assert v.worst is None


def test_vulncount_as_dict() -> None:
    v = VulnCount(critical=1, high=0, moderate=2, low=0)
    d = v.as_dict()
    assert d == {"critical": 1, "high": 0, "moderate": 2, "low": 0, "unknown": 0}


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


def test_parse_npm_audit_json_medium_maps_to_moderate() -> None:
    """'medium' severity key is normalised to moderate."""
    data = {
        "advisories": {
            "1": {"severity": "medium"},
        }
    }
    counts = _parse_npm_audit_json(data)
    assert counts.moderate == 1


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
# CVSS score approximation (cargo-audit mapping)
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


def test_cvss_to_score_confidentiality_only_lands_in_high_bucket() -> None:
    """Averaging C/I/A alone scores this vector ~3.3 (moderate), which would let
    a real High-severity vuln pass ``--fail-on high``. The full CVSS v3.1 base
    score (AV/AC/PR/UI/Scope plus the impact vector) is ~7.5 (high)."""
    score = _cvss_to_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N")
    assert score == 7.5
    assert score >= 7.0  # high bucket per _audit_rust's thresholds


# ---------------------------------------------------------------------------
# govulncheck: unknown bucket
# ---------------------------------------------------------------------------


def test_audit_go_unknown_bucket_on_finding() -> None:
    """govulncheck findings go into the 'unknown' bucket, not 'high'."""
    finding_line = json.dumps({"finding": {"osv": "GO-2024-001"}})
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, finding_line + "\n", ""),
        ),
    ):
        result = _audit_go("/some/repo")
    assert result.status == "vulns"
    assert result.counts.unknown >= 1
    # known buckets must NOT be inflated by govulncheck
    assert result.counts.high == 0
    assert result.counts.critical == 0


def test_audit_go_unknown_bucket_fallback() -> None:
    """Unparseable govulncheck output + non-zero exit → 1 unknown finding."""
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, "not json", "some error"),
        ),
    ):
        result = _audit_go("/some/repo")
    assert result.status == "vulns"
    assert result.counts.unknown >= 1
    assert result.counts.high == 0


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
    assert result.counts.total_with_unknown == 0


# ---------------------------------------------------------------------------
# Exit-code logic: fail_on_unknown
# ---------------------------------------------------------------------------


def test_exit_code_fail_on_unknown_triggers() -> None:
    results = [
        WorkspaceResult(
            label="a",
            path="/a",
            ecosystem="go",
            manager="go",
            status="vulns",
            counts=VulnCount(unknown=2),
        )
    ]
    # Default: fail_on_unknown=False → no exit on unknown
    assert _exit_code_for(results, "high", fail_on_unknown=False) == 0
    # With fail_on_unknown=True → exit 1
    assert _exit_code_for(results, "high", fail_on_unknown=True) == 1


def test_exit_code_fail_on_unknown_never_overrides_fail_on_never() -> None:
    """--fail-on never does NOT suppress --fail-on-unknown."""
    results = [
        WorkspaceResult(
            label="a",
            path="/a",
            ecosystem="go",
            manager="go",
            status="vulns",
            counts=VulnCount(unknown=1),
        )
    ]
    assert _exit_code_for(results, "never", fail_on_unknown=True) == 1


@pytest.mark.parametrize(
    ("status", "counts", "fail_on", "expected"),
    [
        ("vulns", VulnCount(critical=5), "never", 0),
        ("vulns", VulnCount(critical=1), "high", 1),
        ("vulns", VulnCount(high=5), "critical", 0),
        ("ok", None, "high", 0),
    ],
)
def test_exit_code_for_threshold(
    status: str, counts: VulnCount | None, fail_on: str, expected: int
) -> None:
    result = WorkspaceResult(label="a", path="/a", ecosystem="go", manager="go", status=status)
    if counts is not None:
        result.counts = counts
    assert _exit_code_for([result], fail_on) == expected


# ---------------------------------------------------------------------------
# go auditor (mocked subprocess)
# ---------------------------------------------------------------------------


def test_audit_go_skipped_when_govulncheck_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_go("/some/repo")
    assert result.status == "skipped"
    assert "govulncheck" in result.skip_reason


def test_audit_go_error_on_nonzero_no_parse() -> None:
    # Unparseable JSON + non-zero exit → fallback increments unknown count → vulns
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, "not json", "some error"),
        ),
    ):
        result = _audit_go("/some/repo")
    assert result.status == "vulns"
    assert result.counts.unknown >= 1


def test_audit_go_uses_govulncheck_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, float] = {}

    def _fake_run(
        cmd: list[str], cwd: str, *, timeout: float | None = None
    ) -> tuple[int, str, str]:
        if timeout is not None:
            captured["timeout"] = timeout
        return 0, "", ""

    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch("repo_ops_audit.audit._run", side_effect=_fake_run),
    ):
        _audit_go("/some/repo")

    assert captured["timeout"] == 60


# ---------------------------------------------------------------------------
# pip-audit probe
# ---------------------------------------------------------------------------


def test_probe_pip_audit_available() -> None:
    with patch(
        "repo_ops_audit.audit._run",
        return_value=(0, "pip-audit 2.7.0\n", ""),
    ):
        assert _probe_pip_audit("/ws") is True


def test_probe_pip_audit_unavailable() -> None:
    with patch(
        "repo_ops_audit.audit._run",
        return_value=(1, "", "No such command"),
    ):
        assert _probe_pip_audit("/ws") is False


def test_audit_py_uv_skipped_when_uv_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_py_uv("/some/repo")
    assert result.status == "skipped"


def test_audit_py_uv_skipped_when_pip_audit_not_available() -> None:
    """pip-audit unavailable via uv run pip-audit --version → skip."""
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_audit.audit._probe_pip_audit",
            return_value=False,
        ),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "skipped"
    assert "pip-audit" in result.skip_reason


def test_audit_py_uv_ok_no_vulns() -> None:
    pip_audit_out = json.dumps([{"name": "requests", "version": "2.31.0", "vulns": []}])
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_audit.audit._probe_pip_audit", return_value=True),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(0, pip_audit_out, ""),
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
        patch("repo_ops_audit.audit._probe_pip_audit", return_value=True),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(0, pip_audit_out, ""),
        ),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "vulns"
    assert result.counts.high == 1


# ---------------------------------------------------------------------------
# bun availability probe
# ---------------------------------------------------------------------------


def test_probe_bun_audit_available() -> None:
    with patch(
        "repo_ops_audit.audit._run",
        return_value=(0, "Usage: bun audit\n", ""),
    ):
        assert _probe_bun_audit("/ws") is True


def test_probe_bun_audit_unavailable_unknown_command() -> None:
    with patch(
        "repo_ops_audit.audit._run",
        return_value=(1, "", "error: unknown command: audit"),
    ):
        assert _probe_bun_audit("/ws") is False


def test_probe_bun_audit_error_not_unknown_cmd_returns_true() -> None:
    """Non-zero exit that is NOT an 'unknown command' → subcommand assumed present."""
    with patch(
        "repo_ops_audit.audit._run",
        return_value=(1, "", "No package.json found"),
    ):
        assert _probe_bun_audit("/ws") is True


def test_audit_node_bun_skipped_when_audit_subcommand_absent() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=False),
    ):
        result = _audit_node("/some/repo", "bun")
    assert result.status == "skipped"
    assert "bun audit" in result.skip_reason.lower() or "bun" in result.skip_reason


# ---------------------------------------------------------------------------
# node auditor (mocked subprocess)
# ---------------------------------------------------------------------------


def test_audit_node_npm_skipped_when_npm_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "skipped"


def test_audit_node_npm_ok() -> None:
    npm_output = json.dumps(
        {"metadata": {"vulnerabilities": {"critical": 0, "high": 0, "moderate": 0, "low": 0}}}
    )
    with (
        patch("shutil.which", return_value="/usr/bin/npm"),
        patch("repo_ops_audit.audit._run", return_value=(0, npm_output, "")),
    ):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "ok"


def test_audit_node_npm_vulns() -> None:
    npm_output = json.dumps(
        {"metadata": {"vulnerabilities": {"critical": 0, "high": 2, "moderate": 0, "low": 0}}}
    )
    with (
        patch("shutil.which", return_value="/usr/bin/npm"),
        patch("repo_ops_audit.audit._run", return_value=(1, npm_output, "")),
    ):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "vulns"
    assert result.counts.high == 2


def test_audit_node_yarn_ndjson_vulns() -> None:
    """Real ``yarn npm audit --json`` (yarn berry v4) emits newline-delimited
    JSON, one advisory object per line — a single ``json.loads`` over the
    whole output raises JSONDecodeError, which must not be reported as
    "error" (rc != 0) instead of parsing the real findings."""
    yarn_output = "\n".join(
        [
            json.dumps(
                {
                    "value": "shell-quote",
                    "children": {
                        "ID": 1120422,
                        "Issue": "shell-quote quote() does not escape newlines",
                        "URL": "https://github.com/advisories/GHSA-w7jw-789q-3m8p",
                        "Severity": "critical",
                        "Vulnerable Versions": ">=1.1.0 <=1.8.3",
                    },
                }
            ),
            json.dumps(
                {
                    "value": "shell-quote",
                    "children": {
                        "ID": 1123944,
                        "Issue": "shell-quote: Quadratic-complexity DoS",
                        "URL": "https://github.com/advisories/GHSA-395f-4hp3-45gv",
                        "Severity": "high",
                        "Vulnerable Versions": "<=1.8.4",
                    },
                }
            ),
        ]
    )
    with (
        patch("shutil.which", return_value="/usr/bin/yarn"),
        patch("repo_ops_audit.audit._run", return_value=(1, yarn_output, "")),
    ):
        result = _audit_node("/some/repo", "yarn")
    assert result.status == "vulns"
    assert result.error == ""
    assert result.counts.critical == 1
    assert result.counts.high == 1
    assert len(result.findings) == 2
    assert {f.package for f in result.findings} == {"shell-quote"}
    # Yarn's audit JSON has no fix-version field.
    assert result.remediations == []


def test_audit_node_bun_text_parse() -> None:
    bun_output = "2 vulnerabilities (1 high, 1 moderate)"
    with (
        patch("shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch("repo_ops_audit.audit._run", return_value=(1, bun_output, "")),
    ):
        result = _audit_node("/some/repo", "bun")
    assert result.status == "vulns"
    assert result.counts.high == 1
    assert result.counts.moderate == 1
    # Text fallback has no per-package fix-version data.
    assert result.remediations == []


def test_audit_node_bun_json_extracts_remediations() -> None:
    """If bun ever emits npm-advisory-shaped JSON (bun 1.3.14 did not; see
    test_audit_node_bun_native_json_real_shape below), a known-safe fix
    version must still surface as a RemediationItem the same way npm/pnpm/yarn
    do, otherwise apply_remediations never sees a bun workspace to apply
    overrides to."""
    bun_output = json.dumps(
        {
            "vulnerabilities": {
                "lodash": {
                    "via": [{"severity": "high", "url": "https://example/GHSA-1"}],
                    "fixAvailable": {"name": "lodash", "version": "4.17.21"},
                }
            }
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch("repo_ops_audit.audit._run", return_value=(1, bun_output, "")),
    ):
        result = _audit_node("/some/repo", "bun")
    assert result.status == "vulns"
    assert result.counts.high == 1
    assert len(result.remediations) == 1
    rem = result.remediations[0]
    assert rem.package == "lodash"
    assert rem.safe_version == "4.17.21"
    assert rem.ecosystem == "node"


def test_audit_node_bun_native_json_real_shape() -> None:
    """Real ``bun audit --json`` (bun 1.3.14) is package-name -> list[advisory]
    with a flat "severity" field, NOT npm's "vulnerabilities"/"via" schema.
    Feeding that shape into _parse_npm_audit_json silently returns all-zero
    counts, which would make a real 5-vuln finding masquerade as an "error"
    carrying only the "bun audit vX.Y.Z (hash)" stderr banner."""
    bun_output = json.dumps(
        {
            "adm-zip": [{"id": 1, "severity": "high", "url": "https://x/1"}],
            "shell-quote": [
                {"id": 2, "severity": "critical", "url": "https://x/2"},
                {"id": 3, "severity": "high", "url": "https://x/3"},
            ],
            "tmp": [{"id": 4, "severity": "high", "url": "https://x/4"}],
            "uuid": [{"id": 5, "severity": "moderate", "url": "https://x/5"}],
        }
    )
    bun_banner = "\x1b[0m\x1b[1mbun audit \x1b[0m\x1b[2mv1.3.14 (0d9b296a)\x1b[0m"
    with (
        patch("shutil.which", return_value="/usr/bin/bun"),
        patch("repo_ops_audit.audit._probe_bun_audit", return_value=True),
        patch("repo_ops_audit.audit._run", return_value=(1, bun_output, bun_banner)),
    ):
        result = _audit_node("/some/repo", "bun")
    assert result.status == "vulns"
    assert result.error == ""
    assert result.counts.critical == 1
    assert result.counts.high == 3
    assert result.counts.moderate == 1
    assert len(result.findings) == 5
    # Bun's native JSON has no fix-version field.
    assert result.remediations == []


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
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_audit_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--fail-on" in out
    assert "--fail-on-unknown" in out


def test_audit_json_output_empty_roots(tmp_path: Path) -> None:
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


def test_audit_parser_fail_on_unknown_default_off() -> None:
    parser = build_audit_parser()
    args = parser.parse_args([])
    assert args.fail_on_unknown is False


def test_audit_parser_fail_on_unknown_flag() -> None:
    parser = build_audit_parser()
    args = parser.parse_args(["--fail-on-unknown"])
    assert args.fail_on_unknown is True


def test_audit_exclude_filters_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    seen: list[str] = []

    monkeypatch.setattr(
        "repo_ops_audit.audit.discover_repos",
        lambda *args, **kwargs: {
            "keep-common": str(keep),
            "drop-common": str(drop),
        },
    )
    monkeypatch.setattr(
        "repo_ops_audit.audit._detect_workspaces",
        lambda repo: [(repo, "go", "go")],
    )

    def fake_audit(ws: str, ecosystem: str, manager: str) -> WorkspaceResult:
        seen.append(ws)
        return WorkspaceResult(
            label=ws,
            path=ws,
            ecosystem=ecosystem,
            manager=manager,
            status="ok",
        )

    monkeypatch.setattr("repo_ops_audit.audit.audit_workspace", fake_audit)

    assert main([str(tmp_path), "--exclude", "drop"]) == 0
    assert seen == [str(keep)]
    assert "Excluded repos: 1" in capsys.readouterr().out

    assert main([str(tmp_path), "--exclude", "drop", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["excluded"] == 1


def test_audit_exclude_filters_repositories_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    seen: list[str] = []

    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nexclude = ["drop"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_ops_audit.audit.discover_repos",
        lambda *args, **kwargs: {
            "keep-common": str(keep),
            "drop-common": str(drop),
        },
    )
    monkeypatch.setattr("repo_ops_audit.audit.load_global_config", lambda: {})
    monkeypatch.setattr(
        "repo_ops_audit.audit._detect_workspaces",
        lambda repo: [(repo, "go", "go")],
    )

    def fake_audit(ws: str, ecosystem: str, manager: str) -> WorkspaceResult:
        seen.append(ws)
        return WorkspaceResult(
            label=ws,
            path=ws,
            ecosystem=ecosystem,
            manager=manager,
            status="ok",
        )

    monkeypatch.setattr("repo_ops_audit.audit.audit_workspace", fake_audit)

    assert main([str(tmp_path)]) == 0
    assert seen == [str(keep)]
    assert "Excluded repos: 1" in capsys.readouterr().out

    assert main([str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["excluded"] == 1


def test_audit_exclude_unions_cli_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_excluded = tmp_path / "cli-excluded"
    config_excluded = tmp_path / "config-excluded"
    kept = tmp_path / "kept"
    for repo in (cli_excluded, config_excluded, kept):
        repo.mkdir()
    seen: list[str] = []

    (tmp_path / "pyproject.toml").write_text(
        '[tool.repo-ops]\nexclude = ["config-excluded"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "repo_ops_audit.audit.discover_repos",
        lambda *args, **kwargs: {
            "cli": str(cli_excluded),
            "config": str(config_excluded),
            "kept": str(kept),
        },
    )
    monkeypatch.setattr("repo_ops_audit.audit.load_global_config", lambda: {})
    monkeypatch.setattr(
        "repo_ops_audit.audit._detect_workspaces",
        lambda repo: [(repo, "go", "go")],
    )

    def fake_audit(ws: str, ecosystem: str, manager: str) -> WorkspaceResult:
        seen.append(ws)
        return WorkspaceResult(
            label=ws,
            path=ws,
            ecosystem=ecosystem,
            manager=manager,
            status="ok",
        )

    monkeypatch.setattr("repo_ops_audit.audit.audit_workspace", fake_audit)

    assert main(["--exclude", "cli-excluded", str(tmp_path)]) == 0
    assert set(seen) == {str(kept)}
    assert "Excluded repos: 2" in capsys.readouterr().out
