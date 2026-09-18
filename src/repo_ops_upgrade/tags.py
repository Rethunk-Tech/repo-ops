"""Shared git-ls-remote tag resolution — matching, fetching, and SHA-refresh.

Submodule tags and pre-commit hook revs both resolve upstream semver tags
the same way: fetch via ``git ls-remote``, filter to the current rev's
v-prefix style, and take the highest survivor above the current version.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

from repo_ops_core.version_utils import semver_tuple

logger = logging.getLogger(__name__)

_SEMVER_TAG_RE = re.compile(r"^v?(\d+)(?:\.\d+)*(?:[-.][\w.]+)?$")


def is_semver_tag(tag: str) -> bool:
    """True when *tag* looks like a semver version (bare or ``v``-prefixed)."""
    return bool(_SEMVER_TAG_RE.match(tag))


def has_v_prefix(tag: str) -> bool:
    return tag.startswith("v")


def fetch_tag_refs(url: str, *, timeout: int) -> list[tuple[str, str]] | None:
    """Return ``(commit_sha, tag)`` pairs for *url* via ``git ls-remote --tags --refs``.

    Restricts ``GIT_ALLOW_PROTOCOL`` to https/ssh — *url* comes from
    repo-owned config (``.gitmodules`` / ``.pre-commit-config.yaml``) that
    could point anywhere. Returns None on any error so callers degrade to
    report-only rather than raising.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", "--", url],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_ALLOW_PROTOCOL": "https:ssh"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None

    refs: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].startswith("refs/tags/"):
            sha = parts[0].strip()
            tag = parts[1][len("refs/tags/") :]
            if sha and tag:
                refs.append((sha, tag))
    return refs


def resolve_latest_tag(current: str, candidates: list[str]) -> str | None:
    """Return the best candidate tag for *current* from *candidates*, or None.

    Matching rules:
    - *current* must be semver-like; returns None otherwise.
    - Candidates must share the same ``v``-prefix style as *current*.
    - The best across all majors wins.
    - Returns None when already at latest, or no suitable candidate exists.
    """
    if not is_semver_tag(current):
        return None

    want_v = has_v_prefix(current)
    current_tup = semver_tuple(current.lstrip("v"))

    scored: list[tuple[tuple[int, ...], str]] = []
    for tag in candidates:
        if not is_semver_tag(tag) or has_v_prefix(tag) != want_v:
            continue
        scored.append((semver_tuple(tag.lstrip("v")), tag))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)

    best_tup, best_tag = scored[0]
    if best_tup <= current_tup:
        return None  # already at latest
    return best_tag


def resolve_sha_refresh(
    current_sha: str, refs: list[tuple[str, str]], *, context: str
) -> tuple[str, str, str] | None:
    """Resolve a SHA pin to the newest digest within its tagged major.

    The tags attached to the pinned commit establish the version intent.
    Returns ``(latest_sha, current_tag, latest_tag)``, or None when no tag
    matches *current_sha* or no candidate advances past it. *context* names
    the caller in log text (e.g. ``"submodule"``, ``"pre-commit"``).
    """
    normalized = current_sha.lower()
    matching: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        candidate_sha = tag_sha.lower()
        if not (
            candidate_sha == normalized
            or (len(normalized) < 40 and candidate_sha.startswith(normalized))
        ):
            continue
        if not is_semver_tag(tag):
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning("ignoring invalid semver tag %r during %s SHA refresh", tag, context)
            continue
        matching.append((tag_version, tag, tag_sha))

    if not matching:
        return None
    _, current_tag, _ = max(matching, key=lambda item: (item[0], item[1]))
    current_version = semver_tuple(current_tag.lstrip("v"))
    want_v = has_v_prefix(current_tag)
    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        if not is_semver_tag(tag) or has_v_prefix(tag) != want_v:
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning("ignoring invalid semver tag %r during %s SHA refresh", tag, context)
            continue
        if tag_version[0] == current_version[0]:
            candidates.append((tag_version, tag, tag_sha))
    if not candidates:
        return None
    _, latest_tag, latest_sha = max(candidates, key=lambda item: (item[0], item[1]))
    latest_sha_lower = latest_sha.lower()
    if latest_sha_lower == normalized or (
        len(normalized) < 40 and latest_sha_lower.startswith(normalized)
    ):
        return None
    return latest_sha, current_tag, latest_tag
