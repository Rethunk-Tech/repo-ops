"""Pure version string utilities (semver normalize/compare)."""

from __future__ import annotations

import re


def normalize_version(v: str) -> str:
    stripped = re.sub(r"^[\s\^~>=<]+", "", v.strip())
    if stripped.startswith(("v", "V")):
        stripped = stripped[1:]
    return stripped.split("-")[0].split("+")[0]


def semver_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in normalize_version(v).split("."):
        m = re.match(r"(\d+)", p)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def version_satisfies_ceiling(version: str, ceiling: str) -> bool:
    """Return True when *version* does not exceed a hold rule's *ceiling*.

    The ceiling is a version prefix: only the components it specifies are
    constrained, and any component the ceiling omits is unconstrained (an
    implicit "any"). So ``ceiling="6"`` means "major must be <= 6, any
    minor/patch" (blocks a major-7 bump); ``ceiling="5.9"`` means "major < 5,
    or major == 5 and minor <= 9, any patch"; ``ceiling="5.9.2"`` is an exact
    cap.

    Component-wise comparison against the ceiling's own length: the first
    differing component decides; if *version*'s prefix equals the ceiling
    exactly, it satisfies (remaining, unconstrained components are free).
    """
    v_parts = semver_tuple(version)
    c_parts = semver_tuple(ceiling)
    for v_part, c_part in zip(v_parts, c_parts, strict=False):
        if v_part < c_part:
            return True
        if v_part > c_part:
            return False
    # zip() stops at the shorter tuple; if nothing decided it above, every
    # shared component matched the ceiling exactly, so it's satisfied
    # regardless of which side ran out of components first.
    return True
