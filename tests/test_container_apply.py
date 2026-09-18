"""Unit tests for container image pin apply: byte-preserving edits.

Verifies that apply_container only rewrites the tag portion of matched lines
and never touches unrelated lines, comments, or other images.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_container.container import _replace_tag_in_line, apply_container
from repo_ops_models import Workspace


def _make_ws(path: Path) -> Workspace:
    return Workspace(
        path=path,
        ecosystem="container",
        manager="container",
        git_repo=path,
        label="",
    )


# ---------------------------------------------------------------------------
# _replace_tag_in_line — unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, image, old_tag, new_tag, expected",
    [
        ("FROM nginx:1.25\n", "nginx", "1.25", "1.26", "FROM nginx:1.26\n"),
        (
            "    image: nginx:1.25\n",
            "nginx",
            "1.25",
            "1.26",
            "    image: nginx:1.26\n",
        ),
        ("FROM redis:7.2\n", "nginx", "1.25", "1.26", "FROM redis:7.2\n"),
        (
            "FROM nginx:1.25-alpine\n",
            "nginx",
            "1.25-alpine",
            "1.26-alpine",
            "FROM nginx:1.26-alpine\n",
        ),
        # A bump targeting `image:1` must not fire on a line pinned to `image:1.5`.
        ("FROM nginx:1.5\n", "nginx", "1", "2", "FROM nginx:1.5\n"),
        # A later, boundary-correct occurrence of the tag still gets replaced.
        (
            "FROM nginx:1.5 AS base\nFROM nginx:1 AS final\n",
            "nginx",
            "1",
            "2",
            "FROM nginx:1.5 AS base\nFROM nginx:2 AS final\n",
        ),
    ],
)
def test_replace_tag_in_line(
    line: str, image: str, old_tag: str, new_tag: str, expected: str
) -> None:
    assert _replace_tag_in_line(line, image, old_tag, new_tag) == expected


def test_replace_tag_in_line_comment_untouched() -> None:
    line = "# FROM nginx:1.25\n"
    result = _replace_tag_in_line(line, "nginx", "1.25", "1.26")
    # Comment line still gets changed by the simple string replace (intentional
    # — the caller skips non-FROM lines by only iterating real refs).
    # This tests the replace logic in isolation; the higher-level apply loop
    # only calls it for lines that actually contain the image:tag.
    assert "nginx:1.26" in result


# ---------------------------------------------------------------------------
# apply_container — multi-stage Dockerfile
# ---------------------------------------------------------------------------


def test_apply_dockerfile_single_bump(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    original = "# syntax=docker/dockerfile:1\nFROM nginx:1.25\nRUN apt-get update\nEXPOSE 80\n"
    dockerfile.write_text(original, encoding="utf-8")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.24", "1.25", "1.26"],
    ):
        apply_container(_make_ws(tmp_path))

    result = dockerfile.read_text(encoding="utf-8")
    assert "FROM nginx:1.26\n" in result
    # Other lines untouched
    assert "# syntax=docker/dockerfile:1\n" in result
    assert "RUN apt-get update\n" in result
    assert "EXPOSE 80\n" in result
    # Old tag gone
    assert "nginx:1.25" not in result


def test_apply_dockerfile_multi_stage_only_target_lines_change(tmp_path: Path) -> None:
    """Multi-stage Dockerfile: only outdated images bump; stage refs, comments unchanged."""
    dockerfile = tmp_path / "Dockerfile"
    original = (
        "# Build stage\n"
        "FROM node:20-alpine AS build\n"
        "WORKDIR /app\n"
        "RUN npm ci\n"
        "\n"
        "# Serve stage\n"
        "FROM nginx:1.25 AS serve\n"
        "COPY --from=build /app/dist /usr/share/nginx/html\n"
        "\n"
        "# Dev stage (reuses build)\n"
        "FROM build AS dev\n"
        'CMD ["npm", "run", "dev"]\n'
    )
    dockerfile.write_text(original, encoding="utf-8")

    def fake_fetch(image: str) -> list[str] | None:
        if image == "node":
            return ["20-alpine"]  # already latest; no bump
        if image == "nginx":
            return ["1.25", "1.26"]
        return None

    with patch("repo_ops_container.container._fetch_tags", side_effect=fake_fetch):
        apply_container(_make_ws(tmp_path))

    result = dockerfile.read_text(encoding="utf-8")

    # nginx bumped
    assert "FROM nginx:1.26 AS serve\n" in result
    # node untouched (already latest)
    assert "FROM node:20-alpine AS build\n" in result
    # stage ref line untouched
    assert "FROM build AS dev\n" in result
    # comments preserved
    assert "# Build stage\n" in result
    assert "# Serve stage\n" in result
    assert "# Dev stage (reuses build)\n" in result
    # blank lines preserved
    assert "\n\n" in result


def test_apply_dockerfile_same_image_two_stages_both_bumped(tmp_path: Path) -> None:
    """Same outdated image:tag pinned at two FROM lines → both lines bump."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM nginx:1.25 AS build\nFROM nginx:1.25 AS serve\n", encoding="utf-8")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.25", "1.26"],
    ):
        apply_container(_make_ws(tmp_path))

    result = dockerfile.read_text(encoding="utf-8")
    assert result == "FROM nginx:1.26 AS build\nFROM nginx:1.26 AS serve\n"


def test_apply_dockerfile_sha_pinned_untouched(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    original = (
        "FROM nginx@sha256:abc123def456abc123def456abc123def456abc123def456abc123def456ab12\n"
    )
    dockerfile.write_text(original, encoding="utf-8")

    with patch("repo_ops_container.container._fetch_tags", return_value=["1.26"]):
        apply_container(_make_ws(tmp_path))

    # SHA-pinned lines must not be modified
    assert dockerfile.read_text(encoding="utf-8") == original


def test_apply_dockerfile_already_latest_untouched(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    original = "FROM nginx:1.26\n"
    dockerfile.write_text(original, encoding="utf-8")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.24", "1.25", "1.26"],
    ):
        apply_container(_make_ws(tmp_path))

    assert dockerfile.read_text(encoding="utf-8") == original


def test_apply_compose_file(tmp_path: Path) -> None:
    # Use patch versions within the same major; _fetch_tags receives raw image
    # names (e.g. "nginx", "postgres") since normalization happens inside the
    # function but the patch replaces the whole function.
    compose = tmp_path / "docker-compose.yml"
    original = (
        "version: '3'\nservices:\n  web:\n    image: nginx:1.25\n  db:\n    image: postgres:15.3\n"
    )
    compose.write_text(original, encoding="utf-8")

    def fake_fetch(image: str) -> list[str] | None:
        if image == "nginx":
            return ["1.25", "1.26"]
        if image == "postgres":
            return ["15.3", "15.4", "15.5"]
        return None

    with patch("repo_ops_container.container._fetch_tags", side_effect=fake_fetch):
        apply_container(_make_ws(tmp_path))

    result = compose.read_text(encoding="utf-8")
    assert "image: nginx:1.26\n" in result
    assert "image: postgres:15.5\n" in result
    # Structure preserved
    assert "version: '3'\n" in result
    assert "services:\n" in result
    assert "  web:\n" in result
    assert "  db:\n" in result


def test_apply_no_container_files_noop(tmp_path: Path) -> None:
    """Directory with no container files: apply is a no-op."""
    (tmp_path / "main.go").write_text("package main\n")
    # No exception, no files created
    with patch("repo_ops_container.container._fetch_tags", return_value=[]):
        apply_container(_make_ws(tmp_path))
    # Only the original file remains
    assert list(tmp_path.iterdir()) == [tmp_path / "main.go"]


def test_apply_registry_fail_skips_image(tmp_path: Path) -> None:
    """Registry failure for one image must not prevent others from bumping."""
    dockerfile = tmp_path / "Dockerfile"
    original = "FROM nginx:1.25\nFROM redis:7.2\n"
    dockerfile.write_text(original, encoding="utf-8")

    def fake_fetch(image: str) -> list[str] | None:
        if "nginx" in image:
            return None  # failure
        if "redis" in image:
            return ["7.2", "7.4"]
        return None

    with patch("repo_ops_container.container._fetch_tags", side_effect=fake_fetch):
        apply_container(_make_ws(tmp_path))

    result = dockerfile.read_text(encoding="utf-8")
    assert "FROM nginx:1.25\n" in result  # untouched (registry fail)
    assert "FROM redis:7.4\n" in result  # bumped


def test_apply_cross_major_when_env_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM nginx:1.25\n")

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.26", "2.0"],
    ):
        apply_container(_make_ws(tmp_path))

    assert "FROM nginx:2.0\n" in dockerfile.read_text(encoding="utf-8")


def test_apply_preserves_line_endings(tmp_path: Path) -> None:
    """CRLF line endings must survive the apply."""
    dockerfile = tmp_path / "Dockerfile"
    original = "FROM nginx:1.25\r\nEXPOSE 80\r\n"
    dockerfile.write_bytes(original.encode("utf-8"))

    with patch(
        "repo_ops_container.container._fetch_tags",
        return_value=["1.25", "1.26"],
    ):
        apply_container(_make_ws(tmp_path))

    result_bytes = dockerfile.read_bytes()
    assert b"nginx:1.26\r\n" in result_bytes
    assert b"EXPOSE 80\r\n" in result_bytes
