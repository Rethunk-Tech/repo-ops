"""Shared repository exclusion matching and configuration helpers."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from pathlib import Path


def repo_matches_exclude(label: str, path_str: str, patterns: list[str]) -> bool:
    """Return whether any pattern matches a repository label or path."""
    return any(
        pattern in label
        or pattern in path_str
        or fnmatch.fnmatch(label, pattern)
        or fnmatch.fnmatch(path_str, pattern)
        for pattern in patterns
    )


def workspace_matches_exclude(git_repo: Path, workspace: Path, patterns: list[str]) -> bool:
    """Return whether a repo's own pattern names this workspace by its repo-relative path.

    Exact or glob match only: a bare substring like ``mobile`` must not catch
    ``mobile-api`` or every path under a checkout whose name contains it.
    """
    rel = workspace.relative_to(git_repo).as_posix()
    return rel != "." and any(rel == p.strip("/") or fnmatch.fnmatch(rel, p) for p in patterns)


def merge_exclude_patterns(cli_patterns: list[str] | None, *config_excludes: object) -> list[str]:
    """Union CLI and every config layer's patterns, preserving first-seen order.

    Config exclusions are standing policy — a repo listed there is one a sweep
    must never touch — so an ad-hoc ``--exclude`` adds to them rather than
    replacing them.
    """
    merged: list[str] = list(cli_patterns or [])
    for config_exclude in config_excludes:
        if isinstance(config_exclude, list):
            merged.extend(str(pattern) for pattern in config_exclude)
        elif isinstance(config_exclude, str) and config_exclude:
            merged.append(config_exclude)
    return list(dict.fromkeys(merged))


def filter_repo_paths(
    paths: list[Path],
    patterns: list[str],
    *,
    label_fn: Callable[[Path], str] | None = None,
) -> tuple[list[Path], int]:
    """Return paths not excluded by label/path substring or glob matches."""
    if not patterns:
        return paths, 0
    get_label = label_fn or str
    kept: list[Path] = []
    excluded_count = 0
    for path in paths:
        if repo_matches_exclude(get_label(path), str(path), patterns):
            excluded_count += 1
        else:
            kept.append(path)
    return kept, excluded_count
