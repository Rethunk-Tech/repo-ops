"""Tests for SBOM container image and GitHub Actions pin components (#C-8).

Verifies pkg:oci and pkg:github purls appear in collect_components() and in
both the CycloneDX and SPDX documents. Uses real repo-ops-container /
repo-ops-upgrade parsers (both are workspace siblings available in this
dev env) against Dockerfile/compose/workflow fixtures — no network calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
        "      - uses: actions/setup-node@v4.0.1\n"
        "  reusable:\n"
        "    uses: acme/shared/.github/workflows/ci.yml@v1\n"
        "  nested:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: acme/shared/actions/lint@v2\n"
        "      - uses: ./.github/workflows/shared.yml\n"
        "      - uses: docker://alpine@3.20\n",
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
    assert "pkg:github/acme/shared@v1?subpath=.github%2Fworkflows%2Fci.yml" in gh
    assert "pkg:github/acme/shared@v2?subpath=actions%2Flint" in gh
    names = {c["name"] for c in components}
    assert "acme/shared/.github/workflows/ci.yml" in names
    assert "acme/shared/actions/lint" in names
    assert "./.github/workflows/shared.yml" not in names
    assert "docker://alpine" not in names


def test_reusable_workflow_paths_at_same_ref_stay_distinct(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  ci:\n"
        "    uses: acme/shared/.github/workflows/ci.yml@v1\n"
        "  deploy:\n"
        "    uses: acme/shared/.github/workflows/deploy.yml@v1\n",
        encoding="utf-8",
    )

    components = collect_components(tmp_path)
    reusable = [
        component
        for component in components
        if component["name"].startswith("acme/shared/.github/workflows/")
    ]

    assert {component["name"] for component in reusable} == {
        "acme/shared/.github/workflows/ci.yml",
        "acme/shared/.github/workflows/deploy.yml",
    }
    assert {component["purl"] for component in reusable} == {
        "pkg:github/acme/shared@v1?subpath=.github%2Fworkflows%2Fci.yml",
        "pkg:github/acme/shared@v1?subpath=.github%2Fworkflows%2Fdeploy.yml",
    }


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
        "  call-parent:\n"
        "    uses: ../.github/workflows/foo.yml@v1\n"
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


def test_collect_components_degrades_when_container_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing repo_ops_container → no oci components (documented graceful degrade)."""
    import builtins

    _write_dockerfile(tmp_path)
    real_import = builtins.__import__

    def _blocker(name: str, *args: object, **kwargs: object):
        if name == "repo_ops_container" or name.startswith("repo_ops_container."):
            raise ImportError("simulated missing container package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocker)
    components = collect_components(tmp_path)
    assert not any(c["purl"].startswith("pkg:oci/") for c in components)


def test_collect_components_degrades_when_workflow_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing repo_ops_upgrade.workflow_uses → no github components."""
    import builtins

    _write_workflow(tmp_path)
    real_import = builtins.__import__

    def _blocker(name: str, *args: object, **kwargs: object):
        if name == "repo_ops_upgrade" or name.startswith("repo_ops_upgrade."):
            raise ImportError("simulated missing upgrade package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocker)
    components = collect_components(tmp_path)
    assert not any(c["purl"].startswith("pkg:github/") for c in components)
