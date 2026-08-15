"""Rust/Cargo plugin."""

from __future__ import annotations

from repo_ops_rust.rust import apply_rust, check_rust
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("rust", check_rust, apply_rust)
