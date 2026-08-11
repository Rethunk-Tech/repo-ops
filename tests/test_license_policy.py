"""Tests for the repo-ops license subcommand (license-policy gate).

Uses in-memory fixtures; no network or subprocess calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from repo_ops_lockfiles.license_enrich import _cache_path, enrich_license
from repo_ops_lockfiles.license_policy import (
    UNKNOWN_LICENSE,
    PackageLicense,
    _extract_pyproject_license,
    _license_from_metadata,
    _matches_any,
    apply_policy,
    build_license_parser,
    main,
    scan_npm_licenses,
    scan_python_dep_licenses,
    scan_python_licenses,
)
from repo_ops_lockfiles.sbom import build_sbom_parser
from repo_ops_lockfiles.sbom import main as sbom_main

# ---------------------------------------------------------------------------
# Unit: _matches_any
# ---------------------------------------------------------------------------


def test_matches_any_exact() -> None:
    assert _matches_any("MIT", ["MIT", "Apache-2.0"]) is True


def test_matches_any_case_insensitive() -> None:
    assert _matches_any("mit", ["MIT"]) is True
    assert _matches_any("MIT", ["mit"]) is True


def test_matches_any_punctuation_stripped() -> None:
    # "Apache-2.0" vs "Apache 2.0" — both normalise to APACHE20
    assert _matches_any("Apache-2.0", ["Apache 2.0"]) is True


def test_matches_any_no_match() -> None:
    assert _matches_any("GPL-3.0", ["MIT", "Apache-2.0"]) is False


# ---------------------------------------------------------------------------
# Unit: _extract_pyproject_license
# ---------------------------------------------------------------------------


def test_extract_pyproject_license_string() -> None:
    data = {"project": {"name": "foo", "license": "MIT"}}
    assert _extract_pyproject_license(data) == "MIT"


def test_extract_pyproject_license_dict_text() -> None:
    data = {"project": {"name": "foo", "license": {"text": "Apache-2.0"}}}
    assert _extract_pyproject_license(data) == "Apache-2.0"


def test_extract_pyproject_license_classifiers() -> None:
    data = {
        "project": {
            "name": "foo",
            "classifiers": ["License :: OSI Approved :: MIT License"],
        }
    }
    assert _extract_pyproject_license(data) == "MIT License"


def test_extract_pyproject_license_poetry_tool() -> None:
    data = {"tool": {"poetry": {"license": "BSD-3-Clause"}}}
    assert _extract_pyproject_license(data) == "BSD-3-Clause"


def test_extract_pyproject_license_missing() -> None:
    assert _extract_pyproject_license({}) is None
    assert _extract_pyproject_license({"project": {}}) is None


# ---------------------------------------------------------------------------
# Unit: scan_npm_licenses (node_modules)
# ---------------------------------------------------------------------------


def _write_nm_pkg(tmp_path: Path, name: str, version: str, lic: str) -> None:
    pkg_dir = tmp_path / "node_modules" / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_json = pkg_dir / "package.json"
    pkg_json.write_text(
        json.dumps({"name": name, "version": version, "license": lic}),
        encoding="utf-8",
    )


def test_scan_npm_licenses_basic(tmp_path: Path) -> None:
    _write_nm_pkg(tmp_path, "lodash", "4.17.21", "MIT")
    _write_nm_pkg(tmp_path, "express", "4.18.2", "MIT")
    results = scan_npm_licenses(tmp_path)
    names = {r.name for r in results}
    assert "lodash" in names
    assert "express" in names
    assert all(r.ecosystem == "npm" for r in results)


def test_scan_npm_licenses_unknown_when_missing(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "node_modules" / "nolic"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "nolic", "version": "1.0.0"}), encoding="utf-8"
    )
    results = scan_npm_licenses(tmp_path)
    pkg = next(r for r in results if r.name == "nolic")
    assert pkg.license_id == UNKNOWN_LICENSE


def test_scan_npm_licenses_license_dict_type(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "node_modules" / "legacy"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "legacy", "version": "1.0.0", "license": {"type": "ISC"}}),
        encoding="utf-8",
    )
    results = scan_npm_licenses(tmp_path)
    pkg = next(r for r in results if r.name == "legacy")
    assert pkg.license_id == "ISC"


def test_scan_npm_licenses_no_node_modules(tmp_path: Path) -> None:
    assert scan_npm_licenses(tmp_path) == []


def test_scan_npm_licenses_lockfile_fallback_package_lock(tmp_path: Path) -> None:
    """Without node_modules, package-lock.json packages are still scanned (F010)."""
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/lodash": {
                        "name": "lodash",
                        "version": "4.17.21",
                        "license": "MIT",
                    },
                    "node_modules/mystery": {
                        "name": "mystery",
                        "version": "1.0.0",
                    },
                    "": {"name": "root"},
                }
            }
        ),
        encoding="utf-8",
    )
    results = scan_npm_licenses(tmp_path)
    by_name = {r.name: r for r in results}
    assert by_name["lodash"].license_id == "MIT"
    assert by_name["lodash"].source == "package-lock.json"
    assert by_name["mystery"].license_id == UNKNOWN_LICENSE
    assert all(r.ecosystem == "npm" for r in results)


def test_scan_npm_licenses_lockfile_fallback_yarn(tmp_path: Path) -> None:
    """yarn.lock without install yields UNKNOWN entries (fail-closed, not empty)."""
    (tmp_path / "yarn.lock").write_text(
        'lodash@^4.17.21:\n  version "4.17.21"\n',
        encoding="utf-8",
    )
    results = scan_npm_licenses(tmp_path)
    assert len(results) == 1
    assert results[0].name == "lodash"
    assert results[0].license_id == UNKNOWN_LICENSE
    assert results[0].source == "yarn.lock"


def test_scan_npm_licenses_prefers_node_modules_over_lockfile(tmp_path: Path) -> None:
    _write_nm_pkg(tmp_path, "lodash", "4.17.21", "MIT")
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/express": {
                        "name": "express",
                        "version": "4.18.2",
                        "license": "MIT",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    results = scan_npm_licenses(tmp_path)
    names = {r.name for r in results}
    assert names == {"lodash"}
    assert all(r.source == "node_modules" for r in results)


# ---------------------------------------------------------------------------
# Unit: scan_python_licenses (pyproject.toml)
# ---------------------------------------------------------------------------


def test_scan_python_licenses_basic(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.0"\nlicense = "Apache-2.0"\n',
        encoding="utf-8",
    )
    results = scan_python_licenses(tmp_path)
    assert len(results) == 1
    assert results[0].name == "myapp"
    assert results[0].license_id == "Apache-2.0"
    assert results[0].ecosystem == "python"


def test_scan_python_licenses_no_name_skipped(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\nrequires = []\n", encoding="utf-8"
    )
    assert scan_python_licenses(tmp_path) == []


def test_scan_python_licenses_unknown_when_no_license(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "foo"\nversion = "1.0"\n', encoding="utf-8"
    )
    results = scan_python_licenses(tmp_path)
    assert results[0].license_id == UNKNOWN_LICENSE


# ---------------------------------------------------------------------------
# Unit: scan_python_dep_licenses (uv.lock + venv dist-info, #144)
# ---------------------------------------------------------------------------

# Header formats verbatim from real dist-info METADATA files (uv-managed venv):
# packaging 26.2 uses PEP 639 License-Expression; coverage 7.14.3 a classic
# License header; older wheels only carry the trove classifier.
_UV_LOCK = """\
version = 1

[[package]]
name = "packaging"
version = "26.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "coverage"
version = "7.14.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "typing-extensions"
version = "4.12.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "certifi"
version = "2026.6.17"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "myapp"
version = "0.1.0"
source = { editable = "packages/myapp" }
"""


def _write_dist_info(sp: Path, dist: str, version: str, metadata: str) -> None:
    di = sp / f"{dist}-{version}.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text(metadata, encoding="utf-8")


def test_scan_python_dep_licenses_uv_lock_with_venv(tmp_path: Path) -> None:
    """Locked third-party deps resolve licenses from venv dist-info METADATA.

    Covers: License-Expression (PEP 639), classic License header, classifier
    fallback, missing dist-info → UNKNOWN, editable workspace-member exclusion,
    and PEP 503 name normalisation (typing_extensions dir ↔ typing-extensions).
    """
    (tmp_path / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    sp = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    _write_dist_info(
        sp,
        "packaging",
        "26.2",
        "Metadata-Version: 2.4\nName: packaging\nVersion: 26.2\n"
        "License-Expression: Apache-2.0 OR BSD-2-Clause\n\nbody\n",
    )
    _write_dist_info(
        sp,
        "coverage",
        "7.14.3",
        "Metadata-Version: 2.1\nName: coverage\nVersion: 7.14.3\n"
        "License: Apache-2.0\n\nbody\n",
    )
    _write_dist_info(
        sp,
        "typing_extensions",
        "4.12.2",
        "Metadata-Version: 2.1\nName: typing_extensions\nVersion: 4.12.2\n"
        "Classifier: License :: OSI Approved :: Python Software Foundation License\n"
        "\nbody\n",
    )
    # certifi is locked but has no dist-info; myapp is an editable member.

    results = scan_python_dep_licenses(tmp_path)

    by_name = {p.name: p for p in results}
    assert set(by_name) == {"packaging", "coverage", "typing-extensions", "certifi"}
    assert by_name["packaging"].license_id == "Apache-2.0 OR BSD-2-Clause"
    assert by_name["packaging"].source == "uv.lock+dist-info"
    assert by_name["packaging"].version == "26.2"
    assert by_name["coverage"].license_id == "Apache-2.0"
    assert by_name["typing-extensions"].license_id == (
        "Python Software Foundation License"
    )
    assert by_name["certifi"].license_id == UNKNOWN_LICENSE
    assert by_name["certifi"].source == "uv.lock"
    assert all(p.ecosystem == "python" for p in results)


def test_scan_python_dep_licenses_no_venv_all_unknown(tmp_path: Path) -> None:
    """Without a venv, locked packages are reported UNKNOWN (offline; no network)."""
    (tmp_path / "uv.lock").write_text(_UV_LOCK, encoding="utf-8")
    results = scan_python_dep_licenses(tmp_path)
    assert len(results) == 4  # editable myapp still excluded
    assert all(p.license_id == UNKNOWN_LICENSE for p in results)
    assert all(p.source == "uv.lock" for p in results)


def test_scan_python_dep_licenses_poetry_lock(tmp_path: Path) -> None:
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n'
        '\n[[package]]\nname = "certifi"\nversion = "2024.2.2"\n',
        encoding="utf-8",
    )
    results = scan_python_dep_licenses(tmp_path)
    by_name = {p.name: p for p in results}
    assert set(by_name) == {"requests", "certifi"}
    assert all(p.source == "poetry.lock" for p in results)
    assert all(p.license_id == UNKNOWN_LICENSE for p in results)


def test_scan_python_dep_licenses_pdm_lock(tmp_path: Path) -> None:
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "httpx"\nversion = "0.27.0"\n',
        encoding="utf-8",
    )
    results = scan_python_dep_licenses(tmp_path)
    assert len(results) == 1
    assert results[0].name == "httpx"
    assert results[0].source == "pdm.lock"


def test_scan_python_dep_licenses_pipfile_lock(tmp_path: Path) -> None:
    (tmp_path / "Pipfile.lock").write_text(
        json.dumps(
            {
                "default": {"flask": {"version": "==3.0.0"}},
                "develop": {"pytest": {"version": "==8.0.0"}},
            }
        ),
        encoding="utf-8",
    )
    results = scan_python_dep_licenses(tmp_path)
    by_name = {p.name: p for p in results}
    assert set(by_name) == {"flask", "pytest"}
    assert by_name["flask"].version == "3.0.0"
    assert by_name["flask"].source == "Pipfile.lock"


def test_license_from_metadata_rejects_pasted_license_body() -> None:
    """A multi-line License header (pasted license text) is not an identifier."""
    text = (
        "Metadata-Version: 2.1\nName: legacy\nVersion: 1.0\n"
        "License: Permission is hereby granted, free of charge, to any person\n"
        "        obtaining a copy of this software and associated documentation\n"
        "\nbody\n"
    )
    assert _license_from_metadata(text) is None


# ---------------------------------------------------------------------------
# Unit: apply_policy
# ---------------------------------------------------------------------------


def _pkg(name: str, lic: str) -> PackageLicense:
    return PackageLicense(
        name=name,
        version="1.0",
        license_id=lic,
        ecosystem="npm",
        source="node_modules",
    )


def test_apply_policy_no_lists_allows_all() -> None:
    pkgs = [_pkg("lodash", "MIT"), _pkg("express", "MIT")]
    result = apply_policy(pkgs)
    assert result.violations == []
    assert result.unknown == []
    assert len(result.allowed) == 2


def test_apply_policy_unknown_always_in_unknown() -> None:
    pkgs = [_pkg("mystery", UNKNOWN_LICENSE)]
    result = apply_policy(pkgs)
    assert len(result.unknown) == 1
    assert result.violations == []


def test_apply_policy_allow_list_violation() -> None:
    pkgs = [_pkg("lodash", "MIT"), _pkg("gpl-lib", "GPL-3.0")]
    result = apply_policy(pkgs, allow=["MIT", "Apache-2.0"])
    violation_names = {p.name for p in result.violations}
    assert "gpl-lib" in violation_names
    assert "lodash" not in violation_names


def test_apply_policy_deny_list_violation() -> None:
    pkgs = [_pkg("lodash", "MIT"), _pkg("copyleft", "GPL-3.0")]
    result = apply_policy(pkgs, deny=["GPL-3.0", "AGPL-3.0"])
    violation_names = {p.name for p in result.violations}
    assert "copyleft" in violation_names
    assert "lodash" not in violation_names


def test_apply_policy_deny_overrides_allow() -> None:
    """When both allow and deny are set, deny takes precedence."""
    pkgs = [_pkg("dual", "MIT")]
    # Allow MIT but also deny MIT → MIT is flagged as violation
    result = apply_policy(pkgs, allow=["MIT", "Apache-2.0"], deny=["MIT"])
    assert len(result.violations) == 1


def test_apply_policy_all_unknown_no_violations() -> None:
    pkgs = [_pkg("a", UNKNOWN_LICENSE), _pkg("b", UNKNOWN_LICENSE)]
    result = apply_policy(pkgs, deny=["GPL-3.0"])
    assert result.violations == []
    assert len(result.unknown) == 2


def test_apply_policy_spdx_or_expression_not_flagged_when_one_branch_clean() -> None:
    """ "GPL-2.0-only OR MIT" satisfies --deny GPL via the MIT branch alone."""
    pkgs = [_pkg("dual-licensed", "GPL-2.0-only OR MIT")]
    result = apply_policy(pkgs, deny=["GPL"])
    assert result.violations == []
    assert len(result.allowed) == 1


def test_apply_policy_spdx_and_expression_flagged_when_any_part_violates() -> None:
    """ "GPL-2.0-only AND MIT" requires both parts, so the GPL part still violates."""
    pkgs = [_pkg("dual-required", "GPL-2.0-only AND MIT")]
    result = apply_policy(pkgs, deny=["GPL"])
    assert len(result.violations) == 1


# ---------------------------------------------------------------------------
# Integration: happy / violation paths end-to-end
# ---------------------------------------------------------------------------


def test_license_happy_path(tmp_path: Path) -> None:
    """All MIT packages with MIT allow-list → exit 0."""
    _write_nm_pkg(tmp_path, "lodash", "4.17.21", "MIT")
    _write_nm_pkg(tmp_path, "express", "4.18.2", "MIT")
    rc = main(["--allow", "MIT", "--no-fail-unknown", str(tmp_path)])
    assert rc == 0


def test_license_violation_exit_1(tmp_path: Path) -> None:
    """GPL package with MIT allow-list → exit 1."""
    _write_nm_pkg(tmp_path, "gpl-lib", "1.0.0", "GPL-3.0")
    rc = main(["--allow", "MIT", "--no-fail-unknown", str(tmp_path)])
    assert rc == 1


def test_license_deny_list_exit_1(tmp_path: Path) -> None:
    """Denied license found → exit 1."""
    _write_nm_pkg(tmp_path, "copyleft", "1.0.0", "GPL-3.0")
    rc = main(["--deny", "GPL-3.0", "--no-fail-unknown", str(tmp_path)])
    assert rc == 1


def test_license_unknown_exit_2_without_flag(tmp_path: Path) -> None:
    """Unknown licenses → exit 2 by default."""
    _write_nm_pkg(tmp_path, "mystery", "1.0.0", "")
    rc = main([str(tmp_path)])
    # unknown license → exit 2
    assert rc == 2


def test_license_unknown_exit_0_with_flag(tmp_path: Path) -> None:
    """--no-fail-unknown: exit 0 even with unknown licenses."""
    _write_nm_pkg(tmp_path, "mystery", "1.0.0", "")
    rc = main(["--no-fail-unknown", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# CLI: JSON output
# ---------------------------------------------------------------------------


def test_license_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_nm_pkg(tmp_path, "lodash", "4.17.21", "MIT")
    rc = main(["--json", "--compact", "--no-fail-unknown", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "violations" in payload
    assert "unknown" in payload
    assert "summary" in payload
    assert payload["summary"]["scanned"] >= 1


def test_license_json_violation_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_nm_pkg(tmp_path, "gpl-lib", "1.0.0", "GPL-3.0")
    main(["--json", "--compact", "--allow", "MIT", "--no-fail-unknown", str(tmp_path)])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["summary"]["violations"] >= 1
    assert any(v["name"] == "gpl-lib" for v in payload["violations"])


def test_license_enrichment_uses_cached_registry_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/lodash": {
                        "name": "lodash",
                        "version": "4.17.21",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "repo-ops" / "licenses"
    cache_file = _cache_path(cache, "https://registry.npmjs.org/lodash/4.17.21")
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(
        json.dumps({"name": "lodash", "version": "4.17.21", "license": "MIT"}),
        encoding="utf-8",
    )

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    rc = main(
        [
            "--enrich-licenses",
            "--allow",
            "MIT",
            "--json",
            "--compact",
            str(tmp_path),
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["unknown"] == 0
    assert payload["summary"]["allowed"] == 1


def test_license_local_enrichment_skips_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/lodash": {
                        "name": "lodash",
                        "version": "4.17.21",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def network_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("local enrichment must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", network_must_not_run)
    rc = main(
        [
            "--enrich-licenses-local",
            "--json",
            "--compact",
            "--no-fail-unknown",
            str(tmp_path),
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["unknown"] == 1


def test_sbom_local_enrichment_skips_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/lodash": {
                        "name": "lodash",
                        "version": "4.17.21",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def network_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("local enrichment must not use the network")

    monkeypatch.setattr("urllib.request.urlopen", network_must_not_run)
    assert sbom_main(["--enrich-licenses-local", "--compact", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["components"][0]["licenses"][0]["expression"] == "NOASSERTION"


def test_network_enrichment_overrides_local_for_license_and_sbom(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/lodash": {
                        "name": "lodash",
                        "version": "4.17.21",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    xdg_cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    url = "https://registry.npmjs.org/lodash/4.17.21"
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"name": "lodash", "version": "4.17.21", "license": "MIT"}
    ).encode()
    response.__enter__.return_value = response
    calls: list[str] = []

    def network_urlopen(request: object, **kwargs: object) -> MagicMock:
        calls.append(str(getattr(request, "full_url", "")))
        return response

    monkeypatch.setattr("urllib.request.urlopen", network_urlopen)
    rc = main(
        [
            "--enrich-licenses",
            "--enrich-licenses-local",
            "--enrich-licenses-network",
            "--allow",
            "MIT",
            "--json",
            "--compact",
            str(tmp_path),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["unknown"] == 0
    assert calls == [url]

    cache_file = _cache_path(xdg_cache / "repo-ops" / "licenses", url)
    cache_file.unlink()
    assert (
        sbom_main(
            [
                "--enrich-licenses",
                "--enrich-licenses-local",
                "--enrich-licenses-network",
                "--compact",
                str(tmp_path),
            ]
        )
        == 0
    )
    sbom_payload = json.loads(capsys.readouterr().out)
    assert sbom_payload["components"][0]["licenses"][0]["expression"] == "MIT"
    assert calls == [url, url]


def test_network_only_enrichment_for_license_and_sbom(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/lodash": {
                        "name": "lodash",
                        "version": "4.17.21",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    xdg_cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))
    url = "https://registry.npmjs.org/lodash/4.17.21"
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"name": "lodash", "version": "4.17.21", "license": "MIT"}
    ).encode()
    response.__enter__.return_value = response
    calls: list[str] = []

    def network_urlopen(request: object, **kwargs: object) -> MagicMock:
        calls.append(str(getattr(request, "full_url", "")))
        return response

    monkeypatch.setattr("urllib.request.urlopen", network_urlopen)
    enrich_spy = MagicMock(wraps=enrich_license)
    monkeypatch.setattr(
        "repo_ops_lockfiles.license_policy.enrich_license",
        enrich_spy,
    )
    rc = main(
        [
            "--enrich-licenses-network",
            "--allow",
            "MIT",
            "--json",
            "--compact",
            str(tmp_path),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["unknown"] == 0
    assert calls == [url]
    assert enrich_spy.call_args is not None
    assert enrich_spy.call_args.kwargs["allow_network"] is True

    cache_file = _cache_path(xdg_cache / "repo-ops" / "licenses", url)
    cache_file.unlink()
    assert sbom_main(["--enrich-licenses-network", "--compact", str(tmp_path)]) == 0
    sbom_payload = json.loads(capsys.readouterr().out)
    assert sbom_payload["components"][0]["licenses"][0]["expression"] == "MIT"
    assert calls == [url, url]


# ---------------------------------------------------------------------------
# CLI: parser flags
# ---------------------------------------------------------------------------


def test_license_parser_flags() -> None:
    parser = build_license_parser()
    args = parser.parse_args(
        ["--allow", "MIT,Apache-2.0", "--deny", "GPL", "--enrich-licenses"]
    )
    assert args.allow == "MIT,Apache-2.0"
    assert args.deny == "GPL"
    assert args.enrich_licenses is True
    assert args.enrich_licenses_local is False
    assert args.enrich_licenses_network is False


def test_local_enrichment_parser_flag_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    license_args = build_license_parser().parse_args(["--enrich-licenses-local"])
    assert license_args.enrich_licenses_local is True

    monkeypatch.setenv("REPO_OPS_ENRICH_LICENSES_LOCAL", "1")
    license_args = build_license_parser().parse_args([])
    sbom_args = build_sbom_parser().parse_args([])
    assert license_args.enrich_licenses_local is True
    assert sbom_args.enrich_licenses_local is True


def test_network_enrichment_parser_flag_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    license_args = build_license_parser().parse_args(["--enrich-licenses-network"])
    sbom_args = build_sbom_parser().parse_args(["--enrich-licenses-network"])
    assert license_args.enrich_licenses_network is True
    assert sbom_args.enrich_licenses_network is True

    monkeypatch.setenv("REPO_OPS_ENRICH_LICENSES_NETWORK", "1")
    license_args = build_license_parser().parse_args([])
    sbom_args = build_sbom_parser().parse_args([])
    assert license_args.enrich_licenses_network is True
    assert sbom_args.enrich_licenses_network is True


def test_license_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--allow" in out
    assert "--deny" in out
