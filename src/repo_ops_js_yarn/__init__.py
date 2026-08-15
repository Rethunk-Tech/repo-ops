"""yarn package manager plugin."""

from __future__ import annotations

from repo_ops_js_yarn.yarn import apply_yarn, check_yarn_snapshot
from repo_ops_upgrade import registry


def register() -> None:
    # repo_ops_audit.audit._try_register_manager imports this to register a
    # single manager on demand without pulling in every ecosystem plugin.
    registry.register("yarn", check_yarn_snapshot, apply_yarn)
