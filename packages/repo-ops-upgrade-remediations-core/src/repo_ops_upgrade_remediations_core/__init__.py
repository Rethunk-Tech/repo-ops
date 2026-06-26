"""Ecosystem-agnostic remediation framework for repo-ops upgrade hooks."""

from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    dispatch,
    register,
    run_hooks,
)

__all__ = [
    "ApplyResult",
    "RemediationHook",
    "dispatch",
    "register",
    "run_hooks",
]
