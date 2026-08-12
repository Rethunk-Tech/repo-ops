"""Tests for the repo-ops sbom subcommand.

Validates CycloneDX and SPDX document shape, component extraction from fixture
lockfiles, and CLI argument handling.  No network or subprocess calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from repo_ops_lockfiles.sbom import (
    _purl_npm,
    _purl_pypi,
    _split_nv,
    _versions_cargo_lock,
    _versions_go_sum,
    _versions_poetry_uv_lock,
    collect_components,
    emit_cyclonedx,
    emit_spdx,
    main,
)

# ---------------------------------------------------------------------------
# Unit: inline parsers
# ---------------------------------------------------------------------------


def test_versions_poetry_uv_lock_basic(tmp_path: Path) -> None:
    lock = tmp_path / "poetry.lock"
    lock.write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n'
        '\n[[package]]\nname = "certifi"\nversion = "2024.2.2"\n',
        encoding="utf-8",
    )
    result = _versions_poetry_uv_lock(lock)
    assert "requests@2.31.0" in result
    assert "certifi@2024.2.2" in result


def test_versions_uv_lock_nested_dependencies_not_misattributed(tmp_path: Path) -> None:
    """Nested [[package.dependencies]] name= lines must not overwrite the
    top-level package's tracked name before its version is recorded."""
    lock = tmp_path / "uv.lock"
    lock.write_text(
        "version = 1\n"
        "\n"
        "[[package]]\n"
        'name = "requests"\n'
        'version = "2.31.0"\n'
        "\n"
        "[[package.dependencies]]\n"
        'name = "certifi"\n'
        "\n"
        "[[package.dependencies]]\n"
        'name = "idna"\n'
        "\n"
        "[[package]]\n"
        'name = "certifi"\n'
        'version = "2024.2.2"\n',
        encoding="utf-8",
    )
    result = _versions_poetry_uv_lock(lock)
    assert result == {"requests@2.31.0", "certifi@2024.2.2"}


def test_versions_go_sum_basic(tmp_path: Path) -> None:
    gosum = tmp_path / "go.sum"
    gosum.write_text(
        "golang.org/x/net v0.17.0 h1:abc=\ngolang.org/x/net v0.17.0/go.mod h1:xyz=\n",
        encoding="utf-8",
    )
    result = _versions_go_sum(gosum)
    # Both lines add entries; /go.mod suffix stripped from version column
    assert "golang.org/x/net@v0.17.0" in result


def test_versions_cargo_lock_basic(tmp_path: Path) -> None:
    lock = tmp_path / "Cargo.lock"
    lock.write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.197"\n'
        '\n[[package]]\nname = "tokio"\nversion = "1.36.0"\n',
        encoding="utf-8",
    )
    result = _versions_cargo_lock(lock)
    assert "serde@1.0.197" in result
    assert "tokio@1.36.0" in result


# ---------------------------------------------------------------------------
# Unit: PURL helpers
# ---------------------------------------------------------------------------


def test_purl_npm_regular() -> None:
    assert _purl_npm("lodash", "4.17.21") == "pkg:npm/lodash@4.17.21"


def test_purl_npm_scoped() -> None:
    assert _purl_npm("@babel/core", "7.22.0") == "pkg:npm/@babel/core@7.22.0"


def test_purl_pypi_normalises_name() -> None:
    # PEP 503: hyphens/dots/underscores → single hyphen, lowercase
    assert _purl_pypi("My_Package", "1.0") == "pkg:pypi/my-package@1.0"
    assert _purl_pypi("Flask-Cors", "4.0.0") == "pkg:pypi/flask-cors@4.0.0"


def test_split_nv_regular() -> None:
    assert _split_nv("lodash@4.17.21") == ("lodash", "4.17.21")


def test_split_nv_scoped() -> None:
    assert _split_nv("@babel/core@7.22.0") == ("@babel/core", "7.22.0")


def test_split_nv_no_at() -> None:
    name, ver = _split_nv("nover")
    assert name == "nover"
    assert ver == ""


# ---------------------------------------------------------------------------
# Integration: collect_components from fixture lockfiles
# ---------------------------------------------------------------------------


_NPM_LOCK_CONTENT = json.dumps(
    {
        "packages": {
            "node_modules/lodash": {"name": "lodash", "version": "4.17.21"},
            "node_modules/express": {"name": "express", "version": "4.18.2"},
            "": {"name": "root"},
        }
    }
)

_PIPFILE_LOCK_CONTENT = json.dumps(
    {
        "default": {
            "requests": {"version": "==2.31.0"},
            "certifi": {"version": "==2024.2.2"},
        },
        "develop": {},
    }
)


def test_collect_components_npm(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    components = collect_components(tmp_path)
    purls = {c["purl"] for c in components}
    assert "pkg:npm/lodash@4.17.21" in purls
    assert "pkg:npm/express@4.18.2" in purls


def test_collect_components_license_enrichment_is_opt_in(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    package_dir = tmp_path / "node_modules" / "lodash"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps({"name": "lodash", "version": "4.17.21", "license": "MIT"}),
        encoding="utf-8",
    )

    default = collect_components(tmp_path)
    assert next(c for c in default if c["name"] == "lodash")["license"] == "NOASSERTION"

    enriched = collect_components(tmp_path, enrich_licenses=True)
    assert next(c for c in enriched if c["name"] == "lodash")["license"] == "MIT"


def test_collect_components_python(tmp_path: Path) -> None:
    (tmp_path / "Pipfile.lock").write_text(_PIPFILE_LOCK_CONTENT, encoding="utf-8")
    components = collect_components(tmp_path)
    purls = {c["purl"] for c in components}
    assert "pkg:pypi/requests@2.31.0" in purls
    assert "pkg:pypi/certifi@2024.2.2" in purls


def test_collect_components_deduplicates_by_purl(tmp_path: Path) -> None:
    # Two lockfiles with the same package → should appear only once
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    (tmp_path / "yarn.lock").write_text(
        'lodash@^4.17.21:\n  version "4.17.21"\n', encoding="utf-8"
    )
    components = collect_components(tmp_path)
    lodash = [c for c in components if c["name"] == "lodash"]
    assert len(lodash) == 1


def test_collect_components_rust(tmp_path: Path) -> None:
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.197"\n',
        encoding="utf-8",
    )
    components = collect_components(tmp_path)
    purls = {c["purl"] for c in components}
    assert "pkg:cargo/serde@1.0.197" in purls


def test_collect_components_go(tmp_path: Path) -> None:
    gosum = tmp_path / "go.sum"
    gosum.write_text(
        "golang.org/x/net v0.17.0 h1:abc=\n",
        encoding="utf-8",
    )
    components = collect_components(tmp_path)
    purls = {c["purl"] for c in components}
    assert "pkg:golang/golang.org/x/net@v0.17.0" in purls


def test_collect_components_empty_dir(tmp_path: Path) -> None:
    assert collect_components(tmp_path) == []


def test_collect_components_excludes_cargo_registry(tmp_path: Path) -> None:
    """Lockfiles under excluded path fragments (e.g. ~/.cargo/registry/) must
    not be scanned, mirroring discovery.py's should_exclude filtering."""
    excluded_dir = tmp_path / ".cargo" / "registry" / "src" / "some-crate"
    excluded_dir.mkdir(parents=True)
    (excluded_dir / "Cargo.lock").write_text(
        '[[package]]\nname = "should-not-appear"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.197"\n',
        encoding="utf-8",
    )
    components = collect_components(tmp_path)
    names = {c["name"] for c in components}
    assert "serde" in names
    assert "should-not-appear" not in names


# ---------------------------------------------------------------------------
# SBOM document shape: CycloneDX
# ---------------------------------------------------------------------------

_COMPONENTS: list[dict[str, str]] = [
    {
        "type": "library",
        "name": "lodash",
        "version": "4.17.21",
        "purl": "pkg:npm/lodash@4.17.21",
        "bom-ref": "npm:lodash@4.17.21",
    }
]


def test_emit_cyclonedx_schema_keys() -> None:
    doc = emit_cyclonedx(
        _COMPONENTS,
        "my-app",
        tool_version="0.1.0",
        timestamp="2024-01-01T00:00:00+00:00",
        serial="test-serial",
    )
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert doc["serialNumber"] == "urn:uuid:test-serial"
    assert doc["version"] == 1


def test_emit_cyclonedx_metadata() -> None:
    doc = emit_cyclonedx(
        _COMPONENTS,
        "my-app",
        tool_version="1.0.0",
        timestamp="2024-01-01T00:00:00+00:00",
        serial="s",
    )
    meta = doc["metadata"]
    assert isinstance(meta, dict)
    tools = meta["tools"]
    assert tools == {
        "components": [
            {
                "type": "application",
                "publisher": "Rethunk-Tech",
                "authors": [{"name": "Rethunk-Tech"}],
                "name": "repo-ops",
                "version": "1.0.0",
            }
        ]
    }
    assert meta["component"]["name"] == "my-app"


def test_emit_cyclonedx_components() -> None:
    doc = emit_cyclonedx(
        _COMPONENTS,
        "app",
        tool_version="0.1",
        timestamp="t",
        serial="s",
    )
    comps = doc["components"]
    assert isinstance(comps, list)
    assert len(comps) == 1
    c = comps[0]
    assert c["name"] == "lodash"
    assert c["version"] == "4.17.21"
    assert c["purl"] == "pkg:npm/lodash@4.17.21"


def test_emit_cyclonedx_empty_components() -> None:
    doc = emit_cyclonedx([], "app", tool_version="0", timestamp="t", serial="s")
    assert doc["components"] == []


# ---------------------------------------------------------------------------
# SBOM document shape: SPDX
# ---------------------------------------------------------------------------


def test_emit_spdx_schema_keys() -> None:
    doc = emit_spdx(
        _COMPONENTS,
        "my-app",
        tool_version="0.1.0",
        timestamp="2024-01-01T00:00:00+00:00",
        serial="test-serial",
    )
    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["SPDXID"] == "SPDXRef-DOCUMENT"
    assert doc["dataLicense"] == "CC0-1.0"
    assert "packages" in doc
    assert "relationships" in doc


def test_emit_spdx_package_fields() -> None:
    doc = emit_spdx(
        _COMPONENTS,
        "app",
        tool_version="0.1",
        timestamp="t",
        serial="s",
    )
    pkgs = doc["packages"]
    assert isinstance(pkgs, list)
    assert len(pkgs) == 1
    pkg = pkgs[0]
    assert pkg["name"] == "lodash"
    assert pkg["versionInfo"] == "4.17.21"
    assert pkg["filesAnalyzed"] is False
    refs = pkg["externalRefs"]
    assert isinstance(refs, list)
    assert refs[0]["referenceType"] == "purl"
    assert refs[0]["referenceLocator"] == "pkg:npm/lodash@4.17.21"


def test_emit_spdx_relationships() -> None:
    doc = emit_spdx(
        _COMPONENTS,
        "app",
        tool_version="0.1",
        timestamp="t",
        serial="s",
    )
    rels = doc["relationships"]
    assert isinstance(rels, list)
    assert len(rels) == 1
    assert rels[0]["spdxElementId"] == "SPDXRef-DOCUMENT"
    assert rels[0]["relationshipType"] == "DESCRIBES"


def test_emit_spdx_empty_components() -> None:
    doc = emit_spdx([], "app", tool_version="0", timestamp="t", serial="s")
    assert doc["packages"] == []
    assert doc["relationships"] == []


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_sbom_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--format" in out
    assert "--output" in out


def test_sbom_cyclonedx_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    rc = main(["--format", "cyclonedx", "--compact", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["bomFormat"] == "CycloneDX"
    purls = {c["purl"] for c in doc["components"]}
    assert "pkg:npm/lodash@4.17.21" in purls


def test_sbom_spdx_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    rc = main(["--format", "spdx", "--compact", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["spdxVersion"] == "SPDX-2.3"
    names = {p["name"] for p in doc["packages"]}
    assert "lodash" in names


def test_sbom_output_file(tmp_path: Path) -> None:
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    out_file = tmp_path / "sbom.json"
    rc = main(["--format", "cyclonedx", "--output", str(out_file), str(tmp_path)])
    assert rc == 0
    assert out_file.exists()
    doc = json.loads(out_file.read_text())
    assert doc["bomFormat"] == "CycloneDX"


def test_sbom_pretty_print(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "package-lock.json").write_text(_NPM_LOCK_CONTENT, encoding="utf-8")
    rc = main([str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    # Pretty-printed JSON has newlines
    assert "\n" in out
    doc = json.loads(out)
    assert "components" in doc


def test_sbom_empty_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--compact", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["components"] == []


# ---------------------------------------------------------------------------
# Multi-ecosystem collect_components wiring (F075, F073, F074, F077)
# ---------------------------------------------------------------------------


def test_collect_components_uv_and_poetry(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "packaging"\nversion = "26.2"\n',
        encoding="utf-8",
    )
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n',
        encoding="utf-8",
    )
    purls = {c["purl"] for c in collect_components(tmp_path)}
    assert "pkg:pypi/packaging@26.2" in purls
    assert "pkg:pypi/requests@2.31.0" in purls


def test_collect_components_pdm_and_bun(tmp_path: Path) -> None:
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "httpx"\nversion = "0.27.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text(
        json.dumps({"packages": {"lodash@4.17.21": ["lodash@4.17.21"]}}),
        encoding="utf-8",
    )
    purls = {c["purl"] for c in collect_components(tmp_path)}
    assert "pkg:pypi/httpx@0.27.0" in purls
    assert "pkg:npm/lodash@4.17.21" in purls


def test_collect_components_bun_lockb_uses_same_parser(tmp_path: Path) -> None:
    """bun.lockb is wired through versions_bun_lock (text/JSON form in fixtures)."""
    (tmp_path / "bun.lockb").write_text(
        json.dumps({"packages": {"chalk@5.3.0": ["chalk@5.3.0"]}}),
        encoding="utf-8",
    )
    purls = {c["purl"] for c in collect_components(tmp_path)}
    assert "pkg:npm/chalk@5.3.0" in purls


def test_collect_components_requirements_txt(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "requests==2.31.0\n# comment\nflask==3.0.0\n",
        encoding="utf-8",
    )
    purls = {c["purl"] for c in collect_components(tmp_path)}
    assert "pkg:pypi/requests@2.31.0" in purls
    assert "pkg:pypi/flask@3.0.0" in purls


def test_collect_components_deno_npm_and_jsr(tmp_path: Path) -> None:
    (tmp_path / "deno.lock").write_text(
        json.dumps(
            {
                "version": "3",
                "packages": {
                    "specifiers": {
                        "jsr:@std/assert@^0.221.0": "jsr:@std/assert@0.221.0",
                        "npm:chalk@5": "npm:chalk@5.3.0",
                    },
                    "npm": {"chalk@5.3.0": {"integrity": "x"}},
                    "jsr": {"@std/assert@0.221.0": {"integrity": "y"}},
                },
            }
        ),
        encoding="utf-8",
    )
    components = collect_components(tmp_path)
    purls = {c["purl"] for c in components}
    assert "pkg:npm/chalk@5.3.0" in purls
    assert "pkg:jsr/@std/assert@0.221.0" in purls


def test_emit_cyclonedx_includes_license_expression() -> None:
    doc = emit_cyclonedx(
        _COMPONENTS,
        "app",
        tool_version="0.1",
        timestamp="t",
        serial="s",
    )
    comps = doc["components"]
    assert isinstance(comps, list)
    assert comps[0]["licenses"] == [{"expression": "NOASSERTION"}]


def test_emit_spdx_includes_license_concluded() -> None:
    doc = emit_spdx(
        _COMPONENTS,
        "app",
        tool_version="0.1",
        timestamp="t",
        serial="s",
    )
    pkgs = doc["packages"]
    assert isinstance(pkgs, list)
    assert pkgs[0]["licenseConcluded"] == "NOASSERTION"
    assert pkgs[0]["licenseDeclared"] == "NOASSERTION"


def test_collect_components_logs_parse_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt lockfiles log at debug instead of vanishing silently (F071)."""
    import logging

    (tmp_path / "pdm.lock").write_text("not: valid: [[[toml", encoding="utf-8")
    # Force a raising parser by writing a requirements.txt that is fine, and
    # monkeypatch via a file that triggers _safe_parse exception path for go.sum
    # with unreadable content — use chmod if needed. Simpler: patch _versions_go_sum.
    from repo_ops_lockfiles import sbom as sbom_mod

    (tmp_path / "go.sum").write_text(
        "golang.org/x/net v0.17.0 h1:abc=\n", encoding="utf-8"
    )

    def _boom(_p: Path) -> set[str]:
        raise RuntimeError("simulated parse failure")

    with caplog.at_level(logging.DEBUG, logger="repo_ops_lockfiles.sbom"):
        orig = sbom_mod._versions_go_sum
        sbom_mod._versions_go_sum = _boom  # type: ignore[assignment]
        try:
            collect_components(tmp_path)
        finally:
            sbom_mod._versions_go_sum = orig
    assert any("SBOM parse failed" in r.message for r in caplog.records)
