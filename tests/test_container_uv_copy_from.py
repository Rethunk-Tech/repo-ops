"""uv's own image, pinned only by ``COPY --from=ghcr.io/...``, is bumped by tag."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_container.container import apply_container, check_container
from repo_ops_models import Workspace


def _ws(path: Path) -> Workspace:
    return Workspace(path=path, ecosystem="container", manager="container", git_repo=path, label="")


def test_copy_from_ghcr_uv_image_is_bumped_by_tag(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.14.10-slim-bookworm\n"
        "COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/\n"
        "RUN uv sync --locked\n"
    )
    seen: list[str] = []

    def _tags(image: str) -> list[str]:
        seen.append(image)
        return ["0.12.19", "0.13.9"]

    with patch("repo_ops_container.container._fetch_tags", side_effect=_tags):
        changes = check_container(_ws(tmp_path))
        apply_container(_ws(tmp_path))

    assert ("ghcr.io/astral-sh/uv", "0.12.19", "0.13.9") in [
        (c.name.rsplit(":", 1)[0], c.from_ver, c.to) for c in changes
    ]
    # The ghcr.io ref stays registry-qualified through the registry query.
    assert "ghcr.io/astral-sh/uv" in seen
    assert "COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/" in dockerfile.read_text()
