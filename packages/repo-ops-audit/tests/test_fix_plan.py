"""Tests for --fix-plan: remediation extraction and opt-in gating.

All subprocess calls are mocked — no network traffic, no real tool invocations.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from repo_ops_audit.audit import (
    ApplyOutcome,
    RemediationItem,
    VulnCount,
    WorkspaceResult,
    _apply_deno_audit,
    _audit_deno,
    _audit_node,
    _audit_py_uv,
    _audit_rust,
    _deno_import_map_paths,
    _detect_workspaces,
    _extract_remediations_cargo_json,
    _extract_remediations_npm_json,
    _extract_remediations_pip_audit,
    _parse_deno_audit_json,
    _parse_deno_audit_text,
    _plan_deno_fix,
    apply_remediations,
    build_audit_parser,
    collect_all_remediations,
    main,
    print_fix_plan,
)

# ---------------------------------------------------------------------------
# RemediationItem dataclass
# ---------------------------------------------------------------------------


def test_remediation_item_as_dict() -> None:
    item = RemediationItem(
        package="requests",
        safe_version="2.31.0",
        ecosystem="py-uv",
        workspace="/some/ws",
    )
    d = item.as_dict()
    assert d == {
        "package": "requests",
        "safe_version": "2.31.0",
        "ecosystem": "py-uv",
        "workspace": "/some/ws",
    }


# ---------------------------------------------------------------------------
# pip-audit remediation extraction
# ---------------------------------------------------------------------------


def test_extract_remediations_pip_audit_basic() -> None:
    data = [
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
    items = _extract_remediations_pip_audit(data, "/ws")
    assert len(items) == 1
    assert items[0].package == "Pillow"
    assert items[0].safe_version == "9.1.0"
    assert items[0].ecosystem == "py-uv"
    assert items[0].workspace == "/ws"


def test_extract_remediations_pip_audit_takes_highest_fix_version() -> None:
    """When a package has multiple vulns, the highest fix version wins."""
    data = [
        {
            "name": "requests",
            "version": "2.28.0",
            "vulns": [
                {
                    "id": "GHSA-001",
                    "aliases": ["CVE-2023-001"],
                    "fix_versions": ["2.29.0"],
                },
                {
                    "id": "GHSA-002",
                    "aliases": ["CVE-2023-002"],
                    "fix_versions": ["2.31.0"],
                },
                {
                    "id": "GHSA-003",
                    "aliases": ["CVE-2022-001"],
                    "fix_versions": ["2.30.0"],
                },
            ],
        }
    ]
    items = _extract_remediations_pip_audit(data, "/ws")
    assert len(items) == 1
    assert items[0].safe_version == "2.31.0"


def test_extract_remediations_pip_audit_skips_no_fix_versions() -> None:
    """Vulns with empty fix_versions produce no remediation items."""
    data = [
        {
            "name": "cryptography",
            "version": "1.0.0",
            "vulns": [
                {"id": "GHSA-999", "aliases": ["CVE-2023-999"], "fix_versions": []},
            ],
        }
    ]
    items = _extract_remediations_pip_audit(data, "/ws")
    assert items == []


def test_extract_remediations_pip_audit_multiple_packages() -> None:
    data = [
        {
            "name": "requests",
            "version": "2.28.0",
            "vulns": [
                {"id": "G1", "aliases": ["CVE-2023-001"], "fix_versions": ["2.31.0"]}
            ],
        },
        {
            "name": "Pillow",
            "version": "9.0.0",
            "vulns": [
                {"id": "G2", "aliases": ["CVE-2023-002"], "fix_versions": ["9.1.0"]}
            ],
        },
    ]
    items = _extract_remediations_pip_audit(data, "/ws")
    # sorted by package name
    assert len(items) == 2
    names = [i.package for i in items]
    assert "Pillow" in names
    assert "requests" in names


def test_extract_remediations_pip_audit_empty_data() -> None:
    assert _extract_remediations_pip_audit([], "/ws") == []


def test_extract_remediations_pip_audit_no_vulns() -> None:
    data = [{"name": "requests", "version": "2.31.0", "vulns": []}]
    assert _extract_remediations_pip_audit(data, "/ws") == []


# ---------------------------------------------------------------------------
# npm remediation extraction
# ---------------------------------------------------------------------------


def test_extract_remediations_npm_v7_fix_available_dict() -> None:
    """npm v7+ fixAvailable dict → exact safe version."""
    data = {
        "vulnerabilities": {
            "lodash": {
                "name": "lodash",
                "severity": "high",
                "via": [{"url": "https://npmjs.com/advisories/1", "severity": "high"}],
                "fixAvailable": {
                    "name": "lodash",
                    "version": "4.17.21",
                    "isSemVerMajor": False,
                },
            }
        }
    }
    items = _extract_remediations_npm_json(data, "/ws")
    assert len(items) == 1
    assert items[0].package == "lodash"
    assert items[0].safe_version == "4.17.21"
    assert items[0].ecosystem == "node"


def test_extract_remediations_npm_v7_fix_available_true_skipped() -> None:
    """fixAvailable: true (no version) — not actionable, must be skipped."""
    data = {
        "vulnerabilities": {
            "some-pkg": {
                "severity": "moderate",
                "via": [],
                "fixAvailable": True,
            }
        }
    }
    items = _extract_remediations_npm_json(data, "/ws")
    assert items == []


def test_extract_remediations_npm_v7_fix_available_false_skipped() -> None:
    data = {
        "vulnerabilities": {
            "unfixed-pkg": {
                "severity": "critical",
                "via": [],
                "fixAvailable": False,
            }
        }
    }
    items = _extract_remediations_npm_json(data, "/ws")
    assert items == []


def test_extract_remediations_npm_v6_advisories() -> None:
    """npm v6 advisories patched_versions → version constraint string."""
    data = {
        "advisories": {
            "1234": {
                "module_name": "lodash",
                "severity": "high",
                "patched_versions": ">=4.17.21",
            }
        }
    }
    items = _extract_remediations_npm_json(data, "/ws")
    assert len(items) == 1
    assert items[0].package == "lodash"
    assert items[0].safe_version == ">=4.17.21"


def test_extract_remediations_npm_v6_no_patched_versions_skipped() -> None:
    data = {
        "advisories": {
            "5678": {
                "module_name": "some-pkg",
                "severity": "high",
                "patched_versions": "<none>",
            }
        }
    }
    items = _extract_remediations_npm_json(data, "/ws")
    assert items == []


def test_extract_remediations_npm_empty() -> None:
    assert _extract_remediations_npm_json({}, "/ws") == []


# ---------------------------------------------------------------------------
# cargo-audit remediation extraction
# ---------------------------------------------------------------------------


def test_extract_remediations_cargo_basic() -> None:
    data = {
        "vulnerabilities": {
            "list": [
                {
                    "advisory": {
                        "id": "RUSTSEC-2024-001",
                        "package": "regex",
                        "cvss": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                        "patched": {"requirements": [">= 1.5.5"]},
                    },
                    "package": {"name": "regex", "version": "1.5.4"},
                }
            ],
            "count": 1,
            "found": True,
        }
    }
    items = _extract_remediations_cargo_json(data, "/ws")
    assert len(items) == 1
    assert items[0].package == "regex"
    assert items[0].safe_version == ">= 1.5.5"
    assert items[0].ecosystem == "rust"


def test_extract_remediations_cargo_multiple_requirements_joined() -> None:
    data = {
        "vulnerabilities": {
            "list": [
                {
                    "advisory": {
                        "package": "foo",
                        "patched": {"requirements": [">= 1.2.3", "< 2.0.0"]},
                    },
                    "package": {"name": "foo", "version": "1.0.0"},
                }
            ]
        }
    }
    items = _extract_remediations_cargo_json(data, "/ws")
    assert len(items) == 1
    assert ">= 1.2.3" in items[0].safe_version
    assert "< 2.0.0" in items[0].safe_version


def test_extract_remediations_cargo_no_patched_skipped() -> None:
    data = {
        "vulnerabilities": {
            "list": [
                {
                    "advisory": {
                        "package": "bar",
                        "patched": None,
                    },
                    "package": {"name": "bar", "version": "0.1.0"},
                }
            ]
        }
    }
    items = _extract_remediations_cargo_json(data, "/ws")
    assert items == []


def test_extract_remediations_cargo_empty() -> None:
    assert _extract_remediations_cargo_json({}, "/ws") == []


def test_extract_remediations_cargo_empty_list() -> None:
    data = {"vulnerabilities": {"list": [], "count": 0, "found": False}}
    assert _extract_remediations_cargo_json(data, "/ws") == []


# ---------------------------------------------------------------------------
# collect_all_remediations: deduplication
# ---------------------------------------------------------------------------


def test_collect_all_remediations_deduplicates_by_package_ecosystem() -> None:
    """Same (package, ecosystem) from two workspaces → only first kept."""
    r1 = WorkspaceResult(
        label="ws-a",
        path="/ws-a",
        ecosystem="py-uv",
        manager="uv",
        status="vulns",
        counts=VulnCount(high=1),
        remediations=[RemediationItem("requests", "2.31.0", "py-uv", "/ws-a")],
    )
    r2 = WorkspaceResult(
        label="ws-b",
        path="/ws-b",
        ecosystem="py-uv",
        manager="uv",
        status="vulns",
        counts=VulnCount(high=1),
        remediations=[RemediationItem("requests", "2.31.0", "py-uv", "/ws-b")],
    )
    items = collect_all_remediations([r1, r2])
    assert len(items) == 1
    assert items[0].workspace == "/ws-a"


def test_collect_all_remediations_different_ecosystems_not_deduped() -> None:
    """Same package name in different ecosystems → both kept."""
    r1 = WorkspaceResult(
        label="ws-a",
        path="/ws-a",
        ecosystem="py-uv",
        manager="uv",
        status="vulns",
        counts=VulnCount(high=1),
        remediations=[RemediationItem("requests", "2.31.0", "py-uv", "/ws-a")],
    )
    r2 = WorkspaceResult(
        label="ws-b",
        path="/ws-b",
        ecosystem="node",
        manager="npm",
        status="vulns",
        counts=VulnCount(high=1),
        remediations=[RemediationItem("requests", "3.0.0", "node", "/ws-b")],
    )
    items = collect_all_remediations([r1, r2])
    assert len(items) == 2


def test_collect_all_remediations_empty() -> None:
    assert collect_all_remediations([]) == []


def test_collect_all_remediations_no_remediations_on_results() -> None:
    r = WorkspaceResult(
        label="ws",
        path="/ws",
        ecosystem="go",
        manager="go",
        status="vulns",
        counts=VulnCount(unknown=1),
    )
    assert collect_all_remediations([r]) == []


# ---------------------------------------------------------------------------
# Auditor integration: remediations populated in WorkspaceResult
# ---------------------------------------------------------------------------


def test_audit_py_uv_remediations_populated() -> None:
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
        patch("repo_ops_audit.audit._run", return_value=(0, pip_audit_out, "")),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "vulns"
    assert len(result.remediations) == 1
    assert result.remediations[0].package == "Pillow"
    assert result.remediations[0].safe_version == "9.1.0"


def test_audit_py_uv_no_remediations_when_no_fix_versions() -> None:
    pip_audit_out = json.dumps(
        [
            {
                "name": "cryptography",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "GHSA-999",
                        "aliases": ["CVE-2023-999"],
                        "fix_versions": [],
                    }
                ],
            }
        ]
    )
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_audit.audit._probe_pip_audit", return_value=True),
        patch("repo_ops_audit.audit._run", return_value=(0, pip_audit_out, "")),
    ):
        result = _audit_py_uv("/some/repo")
    assert result.status == "vulns"
    assert result.remediations == []


def test_audit_node_npm_remediations_populated() -> None:
    npm_out = json.dumps(
        {
            "vulnerabilities": {
                "lodash": {
                    "severity": "high",
                    "via": [
                        {"url": "https://npmjs.com/advisories/1", "severity": "high"}
                    ],
                    "fixAvailable": {
                        "name": "lodash",
                        "version": "4.17.21",
                        "isSemVerMajor": False,
                    },
                }
            },
            "metadata": {
                "vulnerabilities": {"high": 1, "critical": 0, "moderate": 0, "low": 0}
            },
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/npm"),
        patch("repo_ops_audit.audit._run", return_value=(1, npm_out, "")),
    ):
        result = _audit_node("/some/repo", "npm")
    assert result.status == "vulns"
    assert any(
        r.package == "lodash" and r.safe_version == "4.17.21"
        for r in result.remediations
    )


def test_audit_rust_remediations_populated() -> None:
    cargo_json = json.dumps(
        {
            "vulnerabilities": {
                "list": [
                    {
                        "advisory": {
                            "id": "RUSTSEC-2024-001",
                            "package": "regex",
                            "cvss": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                            "patched": {"requirements": [">= 1.5.5"]},
                        },
                        "package": {"name": "regex", "version": "1.5.4"},
                    }
                ],
                "count": 1,
                "found": True,
            }
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
    assert len(result.remediations) == 1
    assert result.remediations[0].package == "regex"
    assert result.remediations[0].safe_version == ">= 1.5.5"


def test_print_fix_plan_points_at_audit_fix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_fix_plan(
        [
            RemediationItem(
                package="requests",
                safe_version="2.31.0",
                ecosystem="py-uv",
                workspace="/ws",
            )
        ]
    )
    out = capsys.readouterr().out
    assert "repo-ops audit --fix" in out
    assert "planned follow-up" not in out


# ---------------------------------------------------------------------------
# CLI: --fix-plan opt-in gating
# ---------------------------------------------------------------------------


def test_audit_parser_fix_plan_default_off() -> None:
    parser = build_audit_parser()
    args = parser.parse_args([])
    assert args.fix_plan is False


def test_audit_parser_fix_plan_flag() -> None:
    parser = build_audit_parser()
    args = parser.parse_args(["--fix-plan"])
    assert args.fix_plan is True


def test_audit_fix_plan_not_active_by_default(tmp_path: pytest.TempPathFactory) -> None:
    """Without --fix-plan the output must not include remediation sections."""
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
    ):
        rc = main([str(tmp_path)])
    # No vulns in empty repo → exit 0
    assert rc == 0


def test_audit_fix_plan_json_output_no_vulns(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """--fix-plan --json with no vulns → remediations=[] and exit 0."""
    with patch("repo_ops_audit.audit.discover_repos", return_value={}):
        rc = main(["--json", "--fix-plan", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "remediations" in payload
    assert payload["remediations"] == []


def test_audit_fix_plan_json_exits_one_when_remediations(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """--fix-plan --json exits 1 when there are actionable remediations."""
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

    def _mock_run(cmd: list[str], cwd: str, **_kwargs: object) -> tuple[int, str, str]:
        if "--version" in cmd:
            return 0, "pip-audit 2.7.0", ""
        if "pip-audit" in cmd:
            return 0, pip_audit_out, ""
        return 0, "", ""

    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={"/ws": "/ws"}),
        patch(
            "repo_ops_audit.audit._detect_workspaces",
            return_value=[("/ws", "py-uv", "uv")],
        ),
        patch("repo_ops_core.git.repo_label", return_value="ws"),
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_audit.audit._run", side_effect=_mock_run),
    ):
        rc = main(["--json", "--fix-plan", "--fail-on", "never", str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "remediations" in payload
    assert len(payload["remediations"]) == 1
    assert payload["remediations"][0]["package"] == "Pillow"
    assert payload["remediations"][0]["safe_version"] == "9.1.0"


def test_audit_no_fix_plan_json_no_remediations_key(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --fix-plan, JSON output must NOT include a 'remediations' key."""
    with patch("repo_ops_audit.audit.discover_repos", return_value={}):
        rc = main(["--json", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "remediations" not in payload


def test_audit_fix_plan_exits_zero_no_remediations(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """--fix-plan with no remediations → exit 0 (detect-only default is preserved)."""
    with patch("repo_ops_audit.audit.discover_repos", return_value={}):
        rc = main(["--fix-plan", str(tmp_path)])
    assert rc == 0


def test_audit_help_shows_fix_plan(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--fix-plan" in out


# ---------------------------------------------------------------------------
# Deno discovery and audit
# ---------------------------------------------------------------------------


def test_detect_deno_workspace(tmp_path) -> None:
    with (
        patch(
            "repo_ops_audit.audit.iter_scanned_dirs",
            return_value=[(tmp_path, {"deno.json"})],
        ),
        patch("repo_ops_audit.audit.manifest_kinds", return_value=[]),
    ):
        workspaces = _detect_workspaces(str(tmp_path))
    assert workspaces == [(str(tmp_path), "deno", "deno")]


def test_audit_deno_skips_when_tool_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = _audit_deno("/some/repo")
    assert result.status == "skipped"
    assert result.skip_reason == "deno not on PATH"


def test_parse_deno_audit_text_summary_and_fixed_version() -> None:
    text = """\
Found 2 vulnerabilities
Severity: 0 low, 0 moderate, 1 high, 1 critical

Fixed 1 vulnerability:
  lodash 4.17.20 -> 4.17.21
"""
    counts, remediations, findings = _parse_deno_audit_text(text, "/ws")
    assert counts.as_dict() == {
        "critical": 1,
        "high": 1,
        "moderate": 0,
        "low": 0,
        "unknown": 0,
    }
    assert remediations[0].package == "lodash"
    assert remediations[0].safe_version == "4.17.21"
    assert findings[0].ecosystem == "deno"


def test_audit_deno_parses_json_advisories() -> None:
    data = {
        "vulnerabilities": [
            {
                "id": "GHSA-1",
                "package": "oak",
                "severity": "critical",
                "fixed_version": "17.1.0",
            }
        ]
    }
    counts, remediations, findings = _parse_deno_audit_json(data, "/ws")
    assert counts.critical == 1
    assert remediations[0].safe_version == "17.1.0"
    assert findings[0].id == "GHSA-1"


def test_audit_deno_runs_native_audit_and_normalizes_text() -> None:
    output = "Found 1 vulnerability\nSeverity: 0 low, 0 moderate, 1 high, 0 critical\n"
    with (
        patch("shutil.which", return_value="/usr/bin/deno"),
        patch(
            "repo_ops_audit.audit._run_deno_audit",
            return_value=(1, output, ""),
        ) as run_audit,
    ):
        result = _audit_deno("/some/repo")
    run_audit.assert_called_once_with("/some/repo")
    assert result.status == "vulns"
    assert result.counts.high == 1


def test_deno_import_map_escape_is_ignored(tmp_path) -> None:
    outside = tmp_path.parent / "outside-import-map.json"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "deno.json").write_text(
        '{"importMap":"../outside-import-map.json"}', encoding="utf-8"
    )
    assert outside not in _deno_import_map_paths(tmp_path)


def test_plan_deno_fix_restores_lock_import_map_and_manifest(tmp_path) -> None:
    lock = tmp_path / "deno.lock"
    import_map = tmp_path / "imports.json"
    config = tmp_path / "deno.json"
    lock.write_text("original-lock", encoding="utf-8")
    import_map.write_text("original-imports", encoding="utf-8")
    config.write_text('{"importMap":"./imports.json"}', encoding="utf-8")
    output = "Fixed 1 vulnerability:\n  oak 17.0.0 -> 17.1.0\n"

    def fake_run(cmd, cwd, **_kwargs):
        assert cmd == ["deno", "audit", "--fix"]
        lock.write_text("changed-lock", encoding="utf-8")
        import_map.write_text("changed-imports", encoding="utf-8")
        config.write_text('{"importMap":"./other.json"}', encoding="utf-8")
        return 0, output, ""

    with patch("repo_ops_audit.audit._run", side_effect=fake_run):
        remediations = _plan_deno_fix(str(tmp_path))
    assert remediations[0].safe_version == "17.1.0"
    assert lock.read_text(encoding="utf-8") == "original-lock"
    assert import_map.read_text(encoding="utf-8") == "original-imports"
    assert config.read_text(encoding="utf-8") == '{"importMap":"./imports.json"}'


def test_apply_deno_audit_restores_files_when_verification_finds_vulns(
    tmp_path,
) -> None:
    lock = tmp_path / "deno.lock"
    import_map = tmp_path / "import_map.json"
    config = tmp_path / "deno.json"
    lock.write_text("original-lock", encoding="utf-8")
    import_map.write_text("original-imports", encoding="utf-8")
    config.write_text('{"importMap":"./import_map.json"}', encoding="utf-8")
    output = "Found 1 vulnerability\nSeverity: 0 low, 0 moderate, 1 high, 0 critical\n"

    def fake_run(cmd, cwd, **_kwargs):
        if cmd == ["deno", "audit", "--fix"]:
            lock.write_text("changed-lock", encoding="utf-8")
            import_map.write_text("changed-imports", encoding="utf-8")
            config.write_text('{"importMap":"./other.json"}', encoding="utf-8")
            return 0, "", ""
        assert cmd == ["deno", "audit", "--json"]
        return 1, output, ""

    with (
        patch("shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_audit.audit._run", side_effect=fake_run),
    ):
        outcome = _apply_deno_audit(str(tmp_path))
    assert outcome.status == "fail"
    assert lock.read_text(encoding="utf-8") == "original-lock"
    assert import_map.read_text(encoding="utf-8") == "original-imports"
    assert config.read_text(encoding="utf-8") == '{"importMap":"./import_map.json"}'


def test_apply_remediations_dispatches_deno_native_fix(tmp_path) -> None:
    item = RemediationItem("oak", "17.1.0", "deno", str(tmp_path))
    expected = ApplyOutcome(str(tmp_path), "ok", changes_count=1)
    with (
        patch("shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_audit.audit._apply_deno_audit", return_value=expected) as apply,
    ):
        outcomes = apply_remediations([item], do_verify=False)
    assert outcomes == [expected]
    apply.assert_called_once_with(str(tmp_path), do_verify=False)
