"""Rust/Cargo plugin."""

from __future__ import annotations

from repo_ops_rust.rust import apply_rust, check_rust
from repo_ops_upgrade import registry


def register() -> None:
    # repo_ops_audit.audit._try_register_manager imports this to register a
    # single manager on demand without pulling in every ecosystem plugin.
    registry.register("rust", check_rust, apply_rust)
