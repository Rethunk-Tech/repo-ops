"""Go module plugin."""

from __future__ import annotations

from repo_ops_upgrade import registry

from repo_ops_go.go import apply_go, check_go


def register() -> None:
    registry.register("go", check_go, apply_go)
