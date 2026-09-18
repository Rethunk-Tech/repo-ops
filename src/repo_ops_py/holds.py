"""Shared hold-ceiling filtering for pip ecosystems with no per-package
targeted install — a proposed change is dropped up front rather than
attempted and clamped back down after the fact.
"""

from __future__ import annotations

from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_models import Change, Workspace


def filter_held_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    kept: list[Change] = []
    for c in changes:
        rule = resolve_package_hold(ws, c.name)
        if not target_within_hold(c.to, rule):
            continue
        kept.append(c)
    return kept
