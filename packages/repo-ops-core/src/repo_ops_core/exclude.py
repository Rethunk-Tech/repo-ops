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


def merge_exclude_patterns(
    cli_patterns: list[str] | None, config_exclude: object
) -> list[str]:
    """Prefer non-empty CLI patterns, otherwise normalize config patterns."""
    if cli_patterns:
        return list(cli_patterns)
    if isinstance(config_exclude, list):
        return [str(pattern) for pattern in config_exclude]
    if isinstance(config_exclude, str) and config_exclude:
        return [config_exclude]
    return []


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
