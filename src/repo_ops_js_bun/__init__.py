"""bun package manager plugin."""

from __future__ import annotations

from repo_ops_js_bun.bun import apply_bun, check_bun
from repo_ops_upgrade import registry


def register() -> None:
    # repo_ops_audit.audit._try_register_manager imports this to register a
    # single manager on demand without pulling in every ecosystem plugin.
    registry.register("bun", check_bun, apply_bun)
