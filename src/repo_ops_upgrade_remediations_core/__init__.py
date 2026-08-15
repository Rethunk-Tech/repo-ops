"""Ecosystem-agnostic remediation framework for repo-ops upgrade hooks."""

from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    changed_files_since_head,
    dispatch,
    make_fmt_hook,
    register,
    run_hooks,
    stderr_tail,
)

__all__ = [
    "ApplyResult",
    "RemediationHook",
    "changed_files_since_head",
    "dispatch",
    "make_fmt_hook",
    "register",
    "run_hooks",
    "stderr_tail",
]
