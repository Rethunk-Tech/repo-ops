"""npm package manager plugin."""

from __future__ import annotations

from repo_ops_js_npm.npm import apply_npm, check_npm
from repo_ops_upgrade import registry


def register() -> None:
    # repo_ops_audit.audit._try_register_manager imports this to register a
    # single manager on demand without pulling in every ecosystem plugin.
    registry.register("npm", check_npm, apply_npm)
