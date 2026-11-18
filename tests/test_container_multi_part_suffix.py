"""Multi-part tag suffixes (``24-bookworm-slim``) are versioned tags, not opaque ones."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_container.container import _resolve_latest_tag, apply_container, check_container
from repo_ops_models import Workspace


def _ws(path: Path) -> Workspace:
    return Workspace(path=path, ecosystem="container", manager="container", git_repo=path, label="")


def _tags(image: str) -> list[str] | None:
    return {
        "node": ["24-bookworm-slim", "24-bookworm", "26-bookworm-slim"],
        "python": ["3.14.10-slim-bookworm", "3.15.0-slim-bookworm", "3.15.0-bookworm"],
    }.get(image)


def test_latest_tag_keeps_the_whole_multi_part_suffix() -> None:
    assert (
        _resolve_latest_tag("24-bookworm-slim", ["24-bookworm-slim", "26-bookworm-slim"])
        == "26-bookworm-slim"
    )
    # A shorter or different suffix is a different image variant.
    assert _resolve_latest_tag("3.14.10-slim-bookworm", ["3.15.0-bookworm"]) is None


def test_check_and_apply_move_multi_part_suffix_tags(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM node:24-bookworm-slim AS build\n"
        "FROM python:3.14.10-slim-bookworm\n"
        "COPY --from=node:24-bookworm-slim /app /app\n"
    )
    ws = _ws(tmp_path)
    with patch("repo_ops_container.container._fetch_tags", side_effect=_tags):
        changes = check_container(ws)
        apply_container(ws)

    assert {(c.name, c.to) for c in changes} == {
        ("node:24-bookworm-slim", "26-bookworm-slim"),
        ("python:3.14.10-slim-bookworm", "3.15.0-slim-bookworm"),
    }
    assert dockerfile.read_text() == (
        "FROM node:26-bookworm-slim AS build\n"
        "FROM python:3.15.0-slim-bookworm\n"
        "COPY --from=node:26-bookworm-slim /app /app\n"
    )
