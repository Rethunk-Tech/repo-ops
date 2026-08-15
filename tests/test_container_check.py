"""Unit tests for container image pin check: parsing matrix + mocked registry.

No network calls are made; _fetch_tags is monkeypatched throughout.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_container.container import (
    _is_semver_tag,
    _normalize_docker_image,
    _parse_compose_images,
    _parse_from_lines,
    _resolve_latest_tag,
    check_container,
    is_container_workspace,
)
from repo_ops_models import Workspace

# ---------------------------------------------------------------------------
# is_container_workspace
# ---------------------------------------------------------------------------


def test_is_container_workspace_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    assert is_container_workspace(tmp_path) is True


def test_is_container_workspace_dockerfile_suffix(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile.prod").write_text("FROM ubuntu:22.04\n")
    assert is_container_workspace(tmp_path) is True


def test_is_container_workspace_dot_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "app.dockerfile").write_text("FROM alpine:3.19\n")
    assert is_container_workspace(tmp_path) is True


def test_is_container_workspace_compose_yml(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
    assert is_container_workspace(tmp_path) is True


def test_is_container_workspace_compose_yaml(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services:\n  app:\n    image: nginx:1.25\n")
    assert is_container_workspace(tmp_path) is True


def test_is_container_workspace_empty_dir(tmp_path: Path) -> None:
    assert is_container_workspace(tmp_path) is False


def test_is_container_workspace_unrelated_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# readme\n")
    (tmp_path / "main.go").write_text("package main\n")
    assert is_container_workspace(tmp_path) is False


# ---------------------------------------------------------------------------
# _parse_from_lines — parsing matrix
# ---------------------------------------------------------------------------


def test_parse_from_simple_tag() -> None:
    text = "FROM nginx:1.25\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "nginx"
    assert refs[0]["tag"] == "1.25"
    assert refs[0]["sha"] is None
    assert refs[0]["is_stage_ref"] is False


def test_parse_from_no_tag() -> None:
    text = "FROM ubuntu\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "ubuntu"
    assert refs[0]["tag"] is None


def test_parse_from_sha_only() -> None:
    text = "FROM nginx@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["sha"] is not None
    assert refs[0]["sha"].startswith("sha256:")
    assert refs[0]["tag"] is None


def test_parse_from_tag_and_sha() -> None:
    text = "FROM nginx:1.25@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["tag"] == "1.25"
    assert refs[0]["sha"] is not None


def test_parse_from_platform_flag() -> None:
    text = "FROM --platform=linux/amd64 node:20-alpine\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "node"
    assert refs[0]["tag"] == "20-alpine"
    assert refs[0]["is_stage_ref"] is False


def test_parse_from_as_stage() -> None:
    """AS <name> doesn't make the line a stage ref — it DEFINES a stage."""
    text = "FROM node:20-alpine AS builder\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "node"
    assert refs[0]["is_stage_ref"] is False  # defines a stage, not a ref


def test_parse_from_stage_ref_multi_stage() -> None:
    """A FROM that references a previously-defined stage is a stage ref."""
    text = "FROM node:20-alpine AS builder\nFROM builder AS final\n"
    refs = _parse_from_lines(text)
    # first line defines 'builder', is not a stage ref
    assert refs[0]["is_stage_ref"] is False
    assert refs[0]["image"] == "node"
    # second line references 'builder' — is a stage ref
    assert refs[1]["is_stage_ref"] is True
    assert refs[1]["image"] == "builder"


def test_parse_from_scratch_skipped() -> None:
    text = "FROM scratch\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["is_stage_ref"] is True  # scratch is a stage-ref equivalent


def test_parse_from_suffix_tag() -> None:
    text = "FROM nginx:1.25-alpine\n"
    refs = _parse_from_lines(text)
    assert refs[0]["tag"] == "1.25-alpine"


def test_parse_from_ghcr_image() -> None:
    text = "FROM ghcr.io/myorg/myapp:2.0\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "ghcr.io/myorg/myapp"
    assert refs[0]["tag"] == "2.0"


def test_parse_from_multi_stage_full() -> None:
    """Multi-stage Dockerfile with comments — only real image refs returned."""
    text = (
        "# syntax=docker/dockerfile:1\n"
        "FROM node:20-alpine AS build\n"
        "RUN npm install\n"
        "# copy from build\n"
        "FROM nginx:1.25 AS serve\n"
        "COPY --from=build /app /usr/share/nginx/html\n"
        "FROM build AS dev\n"
    )
    refs = _parse_from_lines(text)
    # 3 FROM lines: node (ok), nginx (ok), build (stage ref)
    assert len(refs) == 3
    assert refs[0]["image"] == "node"
    assert refs[0]["is_stage_ref"] is False
    assert refs[1]["image"] == "nginx"
    assert refs[1]["is_stage_ref"] is False
    assert refs[2]["image"] == "build"
    assert refs[2]["is_stage_ref"] is True


# ---------------------------------------------------------------------------
# _parse_compose_images — compose image: lines
# ---------------------------------------------------------------------------


def test_parse_compose_simple() -> None:
    text = (
        "services:\n"
        "  web:\n"
        "    image: nginx:1.25\n"
        "  db:\n"
        "    image: postgres:15-alpine\n"
    )
    refs = _parse_compose_images(text)
    assert len(refs) == 2
    assert refs[0]["image"] == "nginx"
    assert refs[0]["tag"] == "1.25"
    assert refs[1]["image"] == "postgres"
    assert refs[1]["tag"] == "15-alpine"


def test_parse_compose_quoted() -> None:
    text = '    image: "redis:7.2"\n'
    refs = _parse_compose_images(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "redis"
    assert refs[0]["tag"] == "7.2"


def test_parse_compose_no_tag() -> None:
    text = "    image: ubuntu\n"
    refs = _parse_compose_images(text)
    assert len(refs) == 1
    assert refs[0]["tag"] is None


def test_parse_compose_ghcr() -> None:
    text = "    image: ghcr.io/org/app:1.0\n"
    refs = _parse_compose_images(text)
    assert len(refs) == 1
    assert refs[0]["image"] == "ghcr.io/org/app"
    assert refs[0]["tag"] == "1.0"


def test_parse_compose_sha() -> None:
    text = "    image: nginx:1.25@sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
    refs = _parse_compose_images(text)
    assert len(refs) == 1
    assert refs[0]["sha"] is not None
    assert refs[0]["tag"] == "1.25"


# ---------------------------------------------------------------------------
# _is_semver_tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("1.25", True),
        ("1.25.3", True),
        ("20-alpine", True),
        ("15-bookworm", True),
        ("3.19.0", True),
        ("latest", False),
        ("stable", False),
        ("edge", False),
        ("nightly", False),
        ("main", False),
    ],
)
def test_is_semver_tag(tag: str, expected: bool) -> None:
    assert _is_semver_tag(tag) is expected


# ---------------------------------------------------------------------------
# _normalize_docker_image
# ---------------------------------------------------------------------------


def test_normalize_official_image() -> None:
    host, repo = _normalize_docker_image("nginx")
    assert host == "registry-1.docker.io"
    assert repo == "library/nginx"


def test_normalize_user_image() -> None:
    host, repo = _normalize_docker_image("user/myapp")
    assert host == "registry-1.docker.io"
    assert repo == "user/myapp"


def test_normalize_ghcr() -> None:
    host, repo = _normalize_docker_image("ghcr.io/myorg/myapp")
    assert host == "ghcr.io"
    assert repo == "myorg/myapp"


# ---------------------------------------------------------------------------
# _resolve_latest_tag
# ---------------------------------------------------------------------------


def test_resolve_latest_same_major() -> None:
    available = ["1.24", "1.25", "1.26"]
    result = _resolve_latest_tag("1.25", available)
    assert result == "1.26"


def test_resolve_latest_same_suffix() -> None:
    available = ["1.24-alpine", "1.25-alpine", "1.26-alpine", "1.26-slim"]
    result = _resolve_latest_tag("1.25-alpine", available)
    assert result == "1.26-alpine"


def test_resolve_latest_no_cross_major_by_default() -> None:
    """Without allow_cross_major, stays within current major."""
    available = ["1.26", "2.0", "2.1"]
    result = _resolve_latest_tag("1.25", available)
    assert result == "1.26"


def test_resolve_latest_cross_major_when_allowed() -> None:
    available = ["1.26", "2.0", "2.1"]
    result = _resolve_latest_tag("1.25", available, allow_cross_major=True)
    assert result == "2.1"


def test_resolve_latest_already_up_to_date() -> None:
    available = ["1.25", "1.24"]
    result = _resolve_latest_tag("1.25", available)
    assert result is None


def test_resolve_latest_non_semver_tag() -> None:
    """Non-semver current tag → always None."""
    result = _resolve_latest_tag("latest", ["1.25", "1.26"])
    assert result is None


def test_resolve_latest_suffix_mismatch() -> None:
    """No candidates with same suffix → None."""
    available = ["1.26-slim", "1.27-slim"]
    result = _resolve_latest_tag("1.25-alpine", available)
    assert result is None


# ---------------------------------------------------------------------------
# check_container (mocked registry)
# ---------------------------------------------------------------------------


def _make_ws(path: Path) -> Workspace:
    return Workspace(
        path=path,
        ecosystem="container",
        manager="container",
        git_repo=path,
        label="",
    )


def test_check_container_semver_bump(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.24", "1.25", "1.26"],
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].name == "nginx:1.25"
    assert changes[0].from_ver == "1.25"
    assert changes[0].to == "1.26"


def test_check_container_already_latest(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.26\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.24", "1.25", "1.26"],
    ):
        changes = check_container(_make_ws(tmp_path))

    assert changes == []


def test_check_container_sha_pinned_report_only(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        "FROM nginx@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    )

    with patch("repo_ops_container.container._fetch_tags", return_value=[]):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert "[report-only]" in changes[0].to


def test_check_container_non_semver_tag_report_only(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx:latest\n")

    with patch("repo_ops_container.container._fetch_tags", return_value=["1.26"]):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert "[report-only]" in changes[0].to


def test_check_container_no_tag_report_only(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM ubuntu\n")

    with patch("repo_ops_container.container._fetch_tags", return_value=[]):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert "[report-only]" in changes[0].to


def test_check_container_registry_failure_report_only(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25\n")

    with patch("repo_ops_container.container._fetch_tags", return_value=None):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert "[report-only]" in changes[0].to
    assert "registry query failed" in changes[0].to


def test_check_container_scratch_skipped(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")

    with patch("repo_ops_container.container._fetch_tags", return_value=["1.26"]):
        changes = check_container(_make_ws(tmp_path))

    assert changes == []


def test_check_container_stage_ref_skipped(tmp_path: Path) -> None:
    """Stage ref lines must not generate check results."""
    (tmp_path / "Dockerfile").write_text(
        "FROM nginx:1.25 AS base\nFROM base AS runtime\n"
    )

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.25", "1.26"],
    ):
        changes = check_container(_make_ws(tmp_path))

    # only nginx:1.25 → 1.26; not the stage ref
    assert len(changes) == 1
    assert changes[0].name == "nginx:1.25"


def test_check_container_suffix_bump(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25-alpine\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.25-alpine", "1.26-alpine", "1.26-slim"],
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].to == "1.26-alpine"


def test_check_container_compose_file(tmp_path: Path) -> None:
    # Use patch versions within the same major so both images have semver bumps
    # (major version bumps like 15→16 require allow_cross_major and are tested
    # separately via test_check_container_cross_major_env).
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: nginx:1.25\n  db:\n    image: postgres:15.3\n"
    )

    available = {
        "nginx": ["1.25", "1.26"],
        "postgres": ["15.3", "15.4", "15.5"],
    }

    def fake_fetch(image: str) -> list[str] | None:
        name = image.split("/")[-1]
        return available.get(name)

    with patch("repo_ops_container.container._fetch_tags", side_effect=fake_fetch):
        changes = check_container(_make_ws(tmp_path))

    names = {c.name for c in changes}
    assert "nginx:1.25" in names
    assert "postgres:15.3" in names
    nginx_c = next(c for c in changes if "nginx" in c.name)
    assert nginx_c.to == "1.26"
    postgres_c = next(c for c in changes if "postgres" in c.name)
    assert postgres_c.to == "15.5"


def test_check_container_tag_and_sha_report_only(tmp_path: Path, monkeypatch) -> None:
    """tag@sha256:... is treated as sha-pinned → report only.

    Digest refresh defaults on with the sweep (#136); pin it off so this test
    exercises the plain report-only path rather than an actual digest fetch.
    """
    monkeypatch.setenv("REPO_OPS_CONTAINER_REFRESH_SHA", "0")
    (tmp_path / "Dockerfile").write_text(
        "FROM nginx:1.25@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    )

    with patch("repo_ops_container.container._fetch_tags", return_value=["1.26"]):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert "[report-only]" in changes[0].to


def test_check_container_deduplicates_same_image(tmp_path: Path) -> None:
    """Same image:tag referenced in multiple FROM lines → only one Change."""
    (tmp_path / "Dockerfile").write_text(
        "FROM nginx:1.25 AS first\nFROM nginx:1.25 AS second\n"
    )

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.25", "1.26"],
    ):
        changes = check_container(_make_ws(tmp_path))

    # Deduped: only one entry for nginx:1.25
    nginx_changes = [c for c in changes if "nginx:1.25" in c.name]
    assert len(nginx_changes) == 1


def test_check_container_cross_major_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_CONTAINER_UPDATE_LATEST", "1")
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.26", "2.0"],
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].to == "2.0"
