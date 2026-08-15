"""Go-ecosystem upgrade remediations (gofmt, go generate)."""

from repo_ops_upgrade_remediations_go.remediations import (
    REMEDIATIONS,
    RemediationHook,
    run_remediations,
)

__all__ = ["REMEDIATIONS", "RemediationHook", "run_remediations"]
