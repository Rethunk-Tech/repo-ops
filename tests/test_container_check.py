"""Unit tests for container image pin check: parsing matrix + mocked registry.

No network calls are made; _fetch_tags is monkeypatched throughout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
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


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"Dockerfile": "FROM scratch\n"}, True),
        ({"Dockerfile.prod": "FROM ubuntu:22.04\n"}, True),
        ({"app.dockerfile": "FROM alpine:3.19\n"}, True),
        ({"docker-compose.yml": "version: '3'\n"}, True),
        ({"compose.yaml": "services:\n  app:\n    image: nginx:1.25\n"}, True),
        ({"Containerfile": "FROM scratch\n"}, True),
        ({"Containerfile.build": "FROM fedora:44\n"}, True),
        ({"system-scope.Containerfile": "FROM fedora:44\n"}, True),
        ({"llama-cpp.compose.yml": "services:\n  s:\n    image: nginx:1.25\n"}, True),
        ({}, False),
        ({"README.md": "# readme\n", "main.go": "package main\n"}, False),
        # Any `.<suffix>` counts, so Containerfile.md matches exactly as
        # Dockerfile.md always has — the suffix rule is what makes
        # Dockerfile.prod work and it does not inspect the suffix.
        ({"Containerfile.md": "# notes\n"}, True),
        # But a name that merely contains the word must not qualify.
        ({"compose-notes.md": "# notes\n", "my-dockerfile-guide.txt": "x\n"}, False),
    ],
)
def test_is_container_workspace(files: dict[str, str], expected: bool, tmp_path: Path) -> None:
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    assert is_container_workspace(tmp_path) is expected


# ---------------------------------------------------------------------------
# _parse_from_lines — parsing matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "FROM nginx:1.25\n",
            {"image": "nginx", "tag": "1.25", "sha": None, "is_stage_ref": False},
        ),
        ("FROM ubuntu\n", {"image": "ubuntu", "tag": None}),
        (
            "FROM --platform=linux/amd64 node:20-alpine\n",
            {"image": "node", "tag": "20-alpine", "is_stage_ref": False},
        ),
        ("FROM nginx:1.25-alpine\n", {"tag": "1.25-alpine"}),
        (
            "FROM ghcr.io/myorg/myapp:2.0\n",
            {"image": "ghcr.io/myorg/myapp", "tag": "2.0"},
        ),
        # AS <name> DEFINES a stage — it does not make the line a stage ref.
        ("FROM node:20-alpine AS builder\n", {"image": "node", "is_stage_ref": False}),
        # scratch is treated as a stage-ref equivalent (nothing to check upstream).
        ("FROM scratch\n", {"is_stage_ref": True}),
    ],
)
def test_parse_from_single_ref(text: str, expected: dict[str, Any]) -> None:
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    for key, value in expected.items():
        assert refs[0][key] == value


def test_parse_from_sha_only() -> None:
    text = "FROM nginx@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["sha"] is not None
    assert refs[0]["sha"].startswith("sha256:")
    assert refs[0]["tag"] is None


def test_parse_from_tag_and_sha() -> None:
    text = (
        "FROM nginx:1.25@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    )
    refs = _parse_from_lines(text)
    assert len(refs) == 1
    assert refs[0]["tag"] == "1.25"
    assert refs[0]["sha"] is not None


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

_ANY_SHA = object()


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "services:\n  web:\n    image: nginx:1.25\n  db:\n    image: postgres:15-alpine\n",
            [
                {"image": "nginx", "tag": "1.25"},
                {"image": "postgres", "tag": "15-alpine"},
            ],
        ),
        ('    image: "redis:7.2"\n', [{"image": "redis", "tag": "7.2"}]),
        ("    image: ubuntu\n", [{"tag": None}]),
        (
            "    image: ghcr.io/org/app:1.0\n",
            [{"image": "ghcr.io/org/app", "tag": "1.0"}],
        ),
        (
            "    image: nginx:1.25@sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n",
            [{"tag": "1.25", "sha": _ANY_SHA}],
        ),
    ],
)
def test_parse_compose_images(text: str, expected: list[dict[str, Any]]) -> None:
    refs = _parse_compose_images(text)
    assert len(refs) == len(expected)
    for ref, exp in zip(refs, expected, strict=True):
        for key, value in exp.items():
            if value is _ANY_SHA:
                assert ref[key] is not None
            else:
                assert ref[key] == value


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


@pytest.mark.parametrize(
    "image, host, repo",
    [
        ("nginx", "registry-1.docker.io", "library/nginx"),
        ("user/myapp", "registry-1.docker.io", "user/myapp"),
        ("ghcr.io/myorg/myapp", "ghcr.io", "myorg/myapp"),
    ],
)
def test_normalize_docker_image(image: str, host: str, repo: str) -> None:
    assert _normalize_docker_image(image) == (host, repo)


# ---------------------------------------------------------------------------
# _resolve_latest_tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, available, expected",
    [
        ("1.25", ["1.24", "1.25", "1.26"], "1.26"),
        ("1.25-alpine", ["1.24-alpine", "1.25-alpine", "1.26-alpine", "1.26-slim"], "1.26-alpine"),
        # Crosses majors.
        ("1.25", ["1.26", "2.0", "2.1"], "2.1"),
        ("1.25", ["1.25", "1.24"], None),
        # Non-semver current tag → always None.
        ("latest", ["1.25", "1.26"], None),
        # No candidates with same suffix → None.
        ("1.25-alpine", ["1.26-slim", "1.27-slim"], None),
    ],
)
def test_resolve_latest_tag(current: str, available: list[str], expected: str | None) -> None:
    assert _resolve_latest_tag(current, available) == expected


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


@pytest.mark.parametrize(
    "dockerfile, fetch_return",
    [
        ("FROM nginx:1.26\n", ["1.24", "1.25", "1.26"]),
        ("FROM scratch\n", ["1.26"]),
    ],
)
def test_check_container_no_change(
    dockerfile: str, fetch_return: list[str], tmp_path: Path
) -> None:
    (tmp_path / "Dockerfile").write_text(dockerfile)

    with patch("repo_ops_container.container._fetch_tags", return_value=fetch_return):
        changes = check_container(_make_ws(tmp_path))

    assert changes == []


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


def test_check_container_stage_ref_skipped(tmp_path: Path) -> None:
    """Stage ref lines must not generate check results."""
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25 AS base\nFROM base AS runtime\n")

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


def test_check_container_deduplicates_same_image(tmp_path: Path) -> None:
    """Same image:tag referenced in multiple FROM lines → only one Change."""
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25 AS first\nFROM nginx:1.25 AS second\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.25", "1.26"],
    ):
        changes = check_container(_make_ws(tmp_path))

    # Deduped: only one entry for nginx:1.25
    nginx_changes = [c for c in changes if "nginx:1.25" in c.name]
    assert len(nginx_changes) == 1


def test_check_container_crosses_majors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Dockerfile").write_text("FROM nginx:1.25\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.26", "2.0"],
    ):
        changes = check_container(_make_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].to == "2.0"
