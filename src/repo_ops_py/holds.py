"""Shared hold-ceiling filtering for pip ecosystems with no per-package
targeted install — a proposed change is dropped up front rather than
attempted and clamped back down after the fact.
"""

from __future__ import annotations

import re

from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_core.version_utils import pep440_precedence, version_satisfies_ceiling
from repo_ops_models import Change, Workspace
from repo_ops_py.pypi import pypi_releases

_STABLE_RE = re.compile(r"\d+(?:\.\d+)*(?:\.post\d+)?")


def filter_held_changes(ws: Workspace, changes: list[Change]) -> list[Change]:
    kept: list[Change] = []
    for c in changes:
        rule = resolve_package_hold(ws, c.name)
        if not target_within_hold(c.to, rule):
            continue
        kept.append(c)
    return kept


def ceiling_target(ws: Workspace, name: str, latest: str) -> str:
    """*latest*, or the highest stable release within the package's hold ceiling when it exceeds it."""
    rule = resolve_package_hold(ws, name)
    if target_within_hold(latest, rule) or rule is None:
        return latest
    within = [
        v
        for v in pypi_releases(name)
        if _STABLE_RE.fullmatch(v) and version_satisfies_ceiling(v, rule.max_version)
    ]
    return max(within, key=pep440_precedence, default=latest)
