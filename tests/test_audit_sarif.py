"""Tests for SARIF 2.1.0 output and GitHub step-summary in repo-ops audit.

All file I/O is either mocked or uses tmp_path — no network traffic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from repo_ops_audit.audit import (
    RemediationItem,
    VulnCount,
    WorkspaceResult,
    build_sarif_report,
    main,
)
from repo_ops_core.report import build_audit_summary, write_step_summary

# ---------------------------------------------------------------------------
# SARIF document shape
# ---------------------------------------------------------------------------


def _make_remediation(
    package: str = "requests",
    safe_version: str = "2.31.0",
    ecosystem: str = "py-uv",
    workspace: str = "/ws",
) -> RemediationItem:
    return RemediationItem(
        package=package,
        safe_version=safe_version,
        ecosystem=ecosystem,
        workspace=workspace,
    )


def _make_result(
    path: str = "/ws",
    ecosystem: str = "py-uv",
    status: str = "vulns",
    worst: str | None = "high",
) -> WorkspaceResult:
    counts = VulnCount()
    if worst == "high":
        counts.high = 1
    elif worst == "critical":
        counts.critical = 1
    elif worst == "moderate":
        counts.moderate = 1
    elif worst == "low":
        counts.low = 1
    return WorkspaceResult(
        label=path,
        path=path,
        ecosystem=ecosystem,
        manager="uv",
        status=status,
        counts=counts,
    )


def test_sarif_version_and_schema() -> None:
    """SARIF document has correct version and $schema fields."""
    doc = build_sarif_report([], [])
    assert doc["version"] == "2.1.0"
    assert "2.1.0" in doc["$schema"]


def test_sarif_runs_structure() -> None:
    """Top-level 'runs' array with exactly one run."""
    doc = build_sarif_report([], [])
    assert "runs" in doc
    assert isinstance(doc["runs"], list)
    assert len(doc["runs"]) == 1


def test_sarif_tool_driver() -> None:
    """run.tool.driver.name must be 'repo-ops-audit'."""
    doc = build_sarif_report([], [])
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "repo-ops-audit"
    assert "rules" in driver


def test_sarif_one_result_per_remediation() -> None:
    """Each RemediationItem becomes exactly one SARIF result."""
    items = [
        _make_remediation("requests", "2.31.0", "py-uv", "/ws"),
        _make_remediation("Pillow", "9.1.0", "py-uv", "/ws"),
    ]
    results = [_make_result("/ws")]
    doc = build_sarif_report(results, items)
    sarif_results = doc["runs"][0]["results"]
    assert len(sarif_results) == 2  # one per item


def test_sarif_one_rule_per_unique_package() -> None:
    """One SARIF rule per unique (ecosystem, package) pair; duplicates collapsed."""
    items = [
        _make_remediation("lodash", "4.17.21", "node", "/ws-a"),
        _make_remediation(
            "lodash", "4.17.21", "node", "/ws-b"
        ),  # same pkg, different ws
        _make_remediation("express", "4.18.0", "node", "/ws-a"),
    ]
    results = [
        _make_result("/ws-a", "node"),
        _make_result("/ws-b", "node"),
    ]
    doc = build_sarif_report(results, items)
    rules = doc["runs"][0]["tool"]["driver"]["rules"]
    # Two unique packages → two rules
    rule_ids = [r["id"] for r in rules]
    assert len(rule_ids) == 2
    assert len(set(rule_ids)) == 2  # no duplicates


def test_sarif_result_fields() -> None:
    """Each SARIF result has ruleId, level, message, and locations."""
    items = [_make_remediation("requests", "2.31.0", "py-uv", "/ws")]
    results = [_make_result("/ws", "py-uv", worst="high")]
    doc = build_sarif_report(results, items)
    r = doc["runs"][0]["results"][0]
    assert "ruleId" in r
    assert r["ruleId"].startswith("audit/")
    assert "level" in r
    assert r["level"] in ("error", "warning", "note")
    assert "message" in r
    assert "text" in r["message"]
    assert "locations" in r
    assert len(r["locations"]) >= 1


def test_sarif_severity_mapping() -> None:
    """critical/high → error; moderate → warning; low → note."""
    for sev, expected_level in [
        ("critical", "error"),
        ("high", "error"),
        ("moderate", "warning"),
        ("low", "note"),
    ]:
        items = [_make_remediation("pkg", "1.0", "py-uv", "/ws")]
        results = [_make_result("/ws", worst=sev)]
        doc = build_sarif_report(results, items)
        level = doc["runs"][0]["results"][0]["level"]
        assert level == expected_level, (
            f"severity {sev!r} → expected {expected_level!r}, got {level!r}"
        )


def test_sarif_fix_version_in_rule_properties() -> None:
    """Rule properties include the fix_version from RemediationItem."""
    items = [_make_remediation("requests", "2.31.0", "py-uv", "/ws")]
    results = [_make_result("/ws")]
    doc = build_sarif_report(results, items)
    rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
    assert rule["properties"]["fix_version"] == "2.31.0"


def test_sarif_artifact_location_is_repo_relative_not_absolute() -> None:
    """artifactLocation.uri must be repo-relative (uses the scan-root-relative
    label), not the absolute host path, and run.originalUriBaseIds must map
    the %SRCROOT% base used by every result — otherwise GitHub code-scanning
    ingestion cannot resolve the location despite the "upload-ready" claim."""
    abs_ws = "/home/runner/work/some-repo/some-repo/packages/foo"
    items = [_make_remediation("requests", "2.31.0", "py-uv", abs_ws)]
    results = [_make_result(abs_ws)]
    # build_sarif_report reads WorkspaceResult.label directly (already
    # computed by run_audit via repo_label); set it explicitly here.
    results[0].label = "packages/foo"
    doc = build_sarif_report(results, items)
    location = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    uri = location["artifactLocation"]["uri"]
    assert uri == "packages/foo"
    assert not uri.startswith("/")
    assert location["artifactLocation"]["uriBaseId"] == "%SRCROOT%"
    assert "%SRCROOT%" in doc["runs"][0]["originalUriBaseIds"]


def test_sarif_empty_remediations() -> None:
    """Empty remediations → valid SARIF with no rules and no results."""
    doc = build_sarif_report([], [])
    assert doc["runs"][0]["results"] == []
    assert doc["runs"][0]["tool"]["driver"]["rules"] == []


def test_sarif_is_serializable() -> None:
    """build_sarif_report output must be JSON-serializable without errors."""
    items = [
        _make_remediation("requests", "2.31.0", "py-uv", "/ws"),
        _make_remediation("lodash", "4.17.21", "node", "/ws2"),
    ]
    results = [_make_result("/ws"), _make_result("/ws2", "node")]
    doc = build_sarif_report(results, items)
    # Must not raise
    serialized = json.dumps(doc)
    reloaded = json.loads(serialized)
    assert reloaded["version"] == "2.1.0"


# ---------------------------------------------------------------------------
# CLI: --sarif-out flag
# ---------------------------------------------------------------------------


def test_audit_parser_sarif_out_default_none() -> None:
    from repo_ops_audit.audit import build_audit_parser

    parser = build_audit_parser()
    args = parser.parse_args([])
    assert args.sarif_out is None


def test_audit_parser_sarif_out_flag(tmp_path: Path) -> None:
    from repo_ops_audit.audit import build_audit_parser

    parser = build_audit_parser()
    out = str(tmp_path / "findings.sarif")
    args = parser.parse_args(["--sarif-out", out])
    assert args.sarif_out == out


def test_sarif_file_written_on_flag(
    tmp_path: Path,
) -> None:
    """--sarif-out PATH writes a valid SARIF 2.1.0 JSON file."""
    sarif_path = str(tmp_path / "results.sarif")
    with patch("repo_ops_audit.audit.discover_repos", return_value={}):
        rc = main(["--sarif-out", sarif_path, str(tmp_path)])
    assert rc == 0
    with open(sarif_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["version"] == "2.1.0"
    assert "runs" in doc


# ---------------------------------------------------------------------------
# Step-summary writer (report.py)
# ---------------------------------------------------------------------------


def test_write_step_summary_no_env_var(tmp_path: Path) -> None:
    """Returns False when GITHUB_STEP_SUMMARY is not set."""
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
    with patch.dict(os.environ, env, clear=True):
        result = write_step_summary("## test")
    assert result is False


def test_write_step_summary_writes_to_path(
    tmp_path: Path,
) -> None:
    """Returns True and writes content when GITHUB_STEP_SUMMARY is set."""
    summary_file = tmp_path / "summary.md"
    with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
        result = write_step_summary("## Audit\nAll clear.")
    assert result is True
    assert summary_file.exists()
    content = summary_file.read_text(encoding="utf-8")
    assert "## Audit" in content
    assert "All clear." in content


def test_write_step_summary_explicit_path(
    tmp_path: Path,
) -> None:
    """Explicit path= parameter takes precedence over env var."""
    env_file = tmp_path / "env_summary.md"
    explicit_file = tmp_path / "explicit_summary.md"
    with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(env_file)}):
        write_step_summary("explicit content", path=str(explicit_file))
    assert explicit_file.exists()
    assert not env_file.exists()  # env var file not written


def test_write_step_summary_appends(tmp_path: Path) -> None:
    """Multiple write_step_summary calls append to the same file."""
    summary_file = tmp_path / "summary.md"
    with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
        write_step_summary("## First\n")
        write_step_summary("## Second\n")
    content = summary_file.read_text(encoding="utf-8")
    assert "## First" in content
    assert "## Second" in content


def test_build_audit_summary_structure() -> None:
    """build_audit_summary returns markdown with required sections."""
    results = [
        _make_result("/ws", "py-uv", "vulns", "high"),
        _make_result("/ws2", "node", "ok", None),
    ]
    remediations = [_make_remediation("requests", "2.31.0", "py-uv", "/ws")]
    md = build_audit_summary(results, remediations, roots=["/repos"])
    assert "## repo-ops audit" in md
    assert "Total" in md
    assert "requests" in md
    assert "2.31.0" in md


def test_build_audit_summary_no_remediations() -> None:
    """Empty remediations → 'No actionable remediations' message."""
    results = [_make_result("/ws", "py-uv", "ok", None)]
    md = build_audit_summary(results, [], roots=["/repos"])
    assert "No actionable remediations" in md


def test_step_summary_emitted_by_main(
    tmp_path: Path,
) -> None:
    """main() auto-writes to GITHUB_STEP_SUMMARY when the env var is set."""
    summary_file = tmp_path / "summary.md"
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}),
    ):
        rc = main([str(tmp_path)])
    assert rc == 0
    assert summary_file.exists()
    content = summary_file.read_text(encoding="utf-8")
    assert "repo-ops audit" in content
