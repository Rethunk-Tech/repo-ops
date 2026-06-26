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
        "[[package]]\nname = \"requests\"\nversion = \"2.31.0\"\n"
        "\n[[package]]\nname = \"certifi\"\nversion = \"2024.2.2\"\n",
        encoding="utf-8",
    )
    result = _versions_poetry_uv_lock(lock)
    assert "requests@2.31.0" in result
    assert "certifi@2024.2.2" in result


def test_versions_go_sum_basic(tmp_path: Path) -> None:
    gosum = tmp_path / "go.sum"
    gosum.write_text(
        "golang.org/x/net v0.17.0 h1:abc=\n"
        "golang.org/x/net v0.17.0/go.mod h1:xyz=\n",
        encoding="utf-8",
    )
    result = _versions_go_sum(gosum)
    # Both lines add entries; /go.mod suffix stripped from version column
    assert "golang.org/x/net@v0.17.0" in result


def test_versions_cargo_lock_basic(tmp_path: Path) -> None:
    lock = tmp_path / "Cargo.lock"
    lock.write_text(
        "[[package]]\nname = \"serde\"\nversion = \"1.0.197\"\n"
        "\n[[package]]\nname = \"tokio\"\nversion = \"1.36.0\"\n",
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
        "[[package]]\nname = \"serde\"\nversion = \"1.0.197\"\n",
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
    assert doc["specVersion"] == "1.4"
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
    assert isinstance(tools, list)
    assert tools[0]["name"] == "repo-ops"
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


def test_sbom_cyclonedx_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
