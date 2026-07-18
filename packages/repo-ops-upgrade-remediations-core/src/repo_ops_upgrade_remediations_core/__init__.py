"""Ecosystem-agnostic remediation framework for repo-ops upgrade hooks."""

from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    Remediation,
    RemediationHook,
    dispatch,
    register,
    run_hooks,
    stderr_tail,
)

__all__ = [
    "ApplyResult",
    "Remediation",
    "RemediationHook",
    "dispatch",
    "register",
    "run_hooks",
    "stderr_tail",
]
