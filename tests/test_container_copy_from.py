"""Image refs a Dockerfile reaches without a FROM: ``COPY --from=<image>``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_container.container import apply_container, check_container
from repo_ops_models import Workspace


def _ws(path: Path) -> Workspace:
    return Workspace(path=path, ecosystem="container", manager="container", git_repo=path, label="")


def test_copy_from_bun_image_is_bumped_with_its_digest(tmp_path: Path) -> None:
    """The one occurrence in the wild pins bun by tag and digest on a COPY line."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM node:22-slim\n"
        "COPY --from=oven/bun:1.4.0-slim@sha256:"
        + "a"
        * 64
        + " /usr/local/bin/bun /usr/local/bin/bun\n"
        "RUN bun --version\n"
    )

    with (
        patch(
            "repo_ops_container.container._fetch_tags",
            return_value=["1.4.0-slim", "1.4.4-slim"],
        ),
        patch(
            "repo_ops_container.container._fetch_manifest_digest",
            return_value="sha256:" + "b" * 64,
        ),
    ):
        changes = check_container(_ws(tmp_path))
        apply_container(_ws(tmp_path))

    assert [(c.from_ver, c.to) for c in changes] == [
        ("1.4.0-slim@sha256:" + "a" * 64, "1.4.4-slim@sha256:" + "b" * 64)
    ]
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY --from=oven/bun:1.4.4-slim@sha256:" + "b" * 64 in text
    # The FROM line is a different image and stays put.
    assert "FROM node:22-slim\n" in text


def test_copy_from_a_build_stage_is_not_an_image(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM oven/bun:1.4.0-slim AS build\n"
        "RUN bun build ./index.ts\n"
        "FROM node:22-slim\n"
        "COPY --from=build /app/dist /app/dist\n"
        "COPY --from=0 /app/dist /app/dist\n"
    )

    with (
        patch(
            "repo_ops_container.container._fetch_tags", return_value=["1.4.0-slim", "1.4.4-slim"]
        ),
        patch("repo_ops_container.container._fetch_manifest_digest", return_value=None),
    ):
        changes = check_container(_ws(tmp_path))

    # Only the FROM pin; the two stage references are resolved locally.
    assert [c.name for c in changes] == ["oven/bun:1.4.0-slim"]
