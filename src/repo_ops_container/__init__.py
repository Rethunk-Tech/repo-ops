"""Container image pin plugin."""

from __future__ import annotations

from repo_ops_container.container import apply_container, check_container
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("container", check_container, apply_container)
