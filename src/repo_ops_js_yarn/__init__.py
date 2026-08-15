"""yarn package manager plugin."""

from __future__ import annotations

from repo_ops_js_yarn.yarn import apply_yarn, check_yarn_snapshot
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("yarn", check_yarn_snapshot, apply_yarn)
