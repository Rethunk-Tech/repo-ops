"""Shared package version-ceiling ("hold") clamping helpers.

Submodule tags and pre-commit hook revs are both matched against a hold
ceiling the same way: filter candidate tags to the current rev's v-prefix
style, keep only those within the ceiling, and take the highest survivor.
GitHub Actions pins use a different matching policy (always v-prefixed, no
bare-numeric tags) and are resolved separately in ``actions.py``.
"""

from __future__ import annotations

import logging
import re
from functools import cache
from pathlib import Path

from repo_ops_core.holds import HoldRule, load_global_holds, load_repo_holds
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling

logger = logging.getLogger(__name__)

_SEMVER_TAG_RE = re.compile(r"^v?(\d+)(?:\.\d+)*(?:[-.][\w.]+)?$")


@cache
def global_holds_cached() -> tuple[HoldRule, ...]:
    """Process-wide cache of the global hold rules file."""
    return tuple(load_global_holds())


@cache
def repo_holds_cached(repo_root: str) -> tuple[HoldRule, ...]:
    """Process-wide cache of one repo's local hold rules file."""
    return tuple(load_repo_holds(Path(repo_root)))


def _is_semver_tag(tag: str) -> bool:
    return bool(_SEMVER_TAG_RE.match(tag))


def _has_v_prefix(tag: str) -> bool:
    return tag.startswith("v")


def target_within_hold(target: str, rule: HoldRule | None) -> bool:
    """Return whether *target* is allowed by *rule* (``None`` = no active hold)."""
    return rule is None or version_satisfies_ceiling(
        target.lstrip("v"), rule.max_version
    )


def resolve_hold_tag_target(
    current_rev: str, tags: list[str], rule: HoldRule, *, context: str
) -> str | None:
    """Return the highest *tags* entry at or below *rule*, or None.

    None when *current_rev* isn't semver-like, already satisfies the ceiling,
    or no candidate improves on it. *context* names the caller in log text
    (e.g. ``"submodule"``, ``"pre-commit"``).
    """
    if not _is_semver_tag(current_rev):
        return None
    try:
        current_version = semver_tuple(current_rev.lstrip("v"))
    except Exception:
        logger.warning(
            "ignoring invalid semver rev %r while enforcing %s hold",
            current_rev,
            context,
        )
        return None
    if version_satisfies_ceiling(current_rev.lstrip("v"), rule.max_version):
        return None

    want_v = _has_v_prefix(current_rev)
    candidates: list[tuple[tuple[int, ...], str]] = []
    for tag in tags:
        if not _is_semver_tag(tag) or _has_v_prefix(tag) != want_v:
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r while enforcing %s hold",
                tag,
                context,
            )
            continue
        if version_satisfies_ceiling(tag.lstrip("v"), rule.max_version):
            candidates.append((tag_version, tag))

    if not candidates:
        return None
    target_version, target_tag = max(candidates, key=lambda item: (item[0], item[1]))
    if target_version >= current_version:
        return None
    return target_tag


def matching_sha_tags(
    current_sha: str, refs: list[tuple[str, str]], *, context: str
) -> list[tuple[tuple[int, ...], str, str]]:
    """Return semver tags attached to *current_sha*, as ``(version, tag, sha)``."""
    normalized = current_sha.lower()
    matching: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        candidate_sha = tag_sha.lower()
        if not (
            candidate_sha == normalized
            or (len(normalized) < 40 and candidate_sha.startswith(normalized))
        ):
            continue
        if not _is_semver_tag(tag):
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r while enforcing %s hold",
                tag,
                context,
            )
            continue
        matching.append((tag_version, tag, tag_sha))
    return matching


def resolve_hold_sha_target(
    current_sha: str, refs: list[tuple[str, str]], rule: HoldRule, *, context: str
) -> tuple[str, str] | None:
    """Return ``(target_tag, target_sha)`` for the highest held tag, or None."""
    matching_tags = matching_sha_tags(current_sha, refs, context=context)
    if not matching_tags:
        return None
    current_version, current_tag, _ = max(
        matching_tags, key=lambda item: (item[0], item[1])
    )
    if version_satisfies_ceiling(current_tag.lstrip("v"), rule.max_version):
        return None

    want_v = _has_v_prefix(current_tag)
    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        if not _is_semver_tag(tag) or _has_v_prefix(tag) != want_v:
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r while enforcing %s hold",
                tag,
                context,
            )
            continue
        if version_satisfies_ceiling(tag.lstrip("v"), rule.max_version):
            candidates.append((tag_version, tag, tag_sha))

    if not candidates:
        return None
    target_version, target_tag, target_sha = max(
        candidates, key=lambda item: (item[0], item[1])
    )
    if target_version >= current_version:
        return None
    return target_tag, target_sha
