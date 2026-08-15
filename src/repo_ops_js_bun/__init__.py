"""bun package manager plugin."""

from __future__ import annotations

from repo_ops_upgrade import registry

from repo_ops_js_bun.bun import apply_bun, check_bun


def register() -> None:
    registry.register("bun", check_bun, apply_bun)
