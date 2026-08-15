"""npm package manager plugin."""

from __future__ import annotations

from repo_ops_js_npm.npm import apply_npm, check_npm
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("npm", check_npm, apply_npm)
