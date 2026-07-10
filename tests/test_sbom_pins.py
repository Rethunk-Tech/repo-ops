"""Tests for SBOM container image and GitHub Actions pin components (#C-8).

Verifies pkg:oci and pkg:github purls appear in collect_components() and in
both the CycloneDX and SPDX documents. Uses real repo-ops-container /
repo-ops-upgrade parsers (both are workspace siblings available in this
dev env) against Dockerfile/compose/workflow fixtures — no network calls.
"""

from __future__ import annotations

from pathlib import Path

from repo_ops_lockfiles.sbom import (
    collect_components,
    emit_cyclonedx,
    emit_spdx,
)


def _write_dockerfile(root: Path) -> None:
    (root / "Dockerfile").write_text(
        "FROM nginx:1.25-alpine\nCOPY . /app\n", encoding="utf-8"
    )


def _write_workflow(root: Path) -> None:
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-node@v4.0.1\n",
        encoding="utf-8",
    )


def test_collect_components_includes_container_pin(tmp_path: Path) -> None:
    _write_dockerfile(tmp_path)
    components = collect_components(tmp_path)
    oci = [c for c in components if c["purl"].startswith("pkg:oci/")]
    assert len(oci) == 1
    assert oci[0]["name"] == "nginx"
    assert oci[0]["version"] == "1.25-alpine"
    assert oci[0]["purl"] == "pkg:oci/nginx@1.25-alpine"
    assert oci[0]["bom-ref"] == "oci:nginx@1.25-alpine"


def test_collect_components_includes_actions_pins(tmp_path: Path) -> None:
    _write_workflow(tmp_path)
    components = collect_components(tmp_path)
    gh = {c["purl"] for c in components if c["purl"].startswith("pkg:github/")}
    assert "pkg:github/actions/checkout@v4" in gh
    assert "pkg:github/actions/setup-node@v4.0.1" in gh


def test_collect_components_dockerfile_stage_ref_excluded(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        "FROM golang:1.22 AS builder\nFROM builder\n", encoding="utf-8"
    )
    components = collect_components(tmp_path)
    oci = [c for c in components if c["purl"].startswith("pkg:oci/")]
    # Only the real base image is a component; "FROM builder" is a stage ref.
    assert len(oci) == 1
    assert oci[0]["name"] == "golang"


def test_local_reusable_workflow_ref_excluded_from_pins(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  call:\n"
        "    uses: ./.github/workflows/shared.yml\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    components = collect_components(tmp_path)
    gh = {c["purl"] for c in components if c["purl"].startswith("pkg:github/")}
    assert gh == {"pkg:github/actions/checkout@v4"}


def test_container_and_actions_pins_present_in_cyclonedx(tmp_path: Path) -> None:
    _write_dockerfile(tmp_path)
    _write_workflow(tmp_path)
    components = collect_components(tmp_path)
    doc = emit_cyclonedx(components, "myrepo")
    purls = {c["purl"] for c in doc["components"]}
    assert "pkg:oci/nginx@1.25-alpine" in purls
    assert "pkg:github/actions/checkout@v4" in purls


def test_container_and_actions_pins_present_in_spdx(tmp_path: Path) -> None:
    _write_dockerfile(tmp_path)
    _write_workflow(tmp_path)
    components = collect_components(tmp_path)
    doc = emit_spdx(components, "myrepo")
    purls = {
        ref["referenceLocator"]
        for pkg in doc["packages"]
        for ref in pkg["externalRefs"]
    }
    assert "pkg:oci/nginx@1.25-alpine" in purls
    assert "pkg:github/actions/checkout@v4" in purls
