"""Rust-ecosystem upgrade remediations (cargo fmt)."""

from repo_ops_upgrade_remediations_rust.remediations import (
    REMEDIATIONS,
    RemediationHook,
    run_remediations,
)

__all__ = ["REMEDIATIONS", "RemediationHook", "run_remediations"]
