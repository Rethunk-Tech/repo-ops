"""Shared hold-ceiling filtering for pip ecosystems with no per-package
targeted install — a proposed change is dropped up front rather than
attempted and clamped back down after the fact.
"""

from __future__ import annotations

from repo_ops_core.holds import resolve_package_hold
from repo_ops_core.version_utils import version_satisfies_ceiling
from repo_ops_models import Change, Workspace


def filter_held_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    kept: list[Change] = []
    for c in changes:
        rule = resolve_package_hold(ws, c.name)
        if rule is not None and not version_satisfies_ceiling(c.to, rule.max_version):
            continue
        kept.append(c)
    return kept
