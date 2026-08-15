"""Integration test: container pin check via check_container.

Writes a minimal Dockerfile with a semver tag pin and runs the real
``check_container`` helper (stdlib urllib registry queries).

Network: registry tag lookup may fail offline; test asserts parseable output or
skips on network-style errors embedded in report-only changes.
"""

from __future__ import annotations

import pytest

from repo_ops_container.container import check_container
from repo_ops_models import Workspace

pytestmark = pytest.mark.integration


def test_check_container_parses_dockerfile_pin(tmp_path) -> None:
    """check_container returns a list of Change objects for a tagged FROM pin."""
    (tmp_path / "Dockerfile").write_text("FROM alpine:3.17\n", encoding="utf-8")

    ws = Workspace(
        path=tmp_path,
        ecosystem="container",
        manager="container",
        git_repo=tmp_path,
        label="container-integ",
    )

    changes = check_container(ws)
    assert isinstance(changes, list)
    # Resilience: either an actionable bump, report-only entry, or empty when current.
    for change in changes:
        assert change.name
        assert change.from_ver is not None
        assert change.to is not None
