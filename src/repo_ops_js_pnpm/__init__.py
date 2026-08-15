"""pnpm package manager plugin."""

from __future__ import annotations

from repo_ops_js_pnpm.pnpm import apply_pnpm, check_pnpm
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("pnpm", check_pnpm, apply_pnpm)
