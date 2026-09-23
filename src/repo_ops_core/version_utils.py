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


def version_parts(version: str) -> tuple[int, int, int]:
    """Extract a padded (major, minor, patch) triple from anywhere in *version*.

    Unlike :func:`semver_tuple` (which normalizes a version string that
    starts with the number), this searches for the first digit run — needed
    for a manager's freeform ``latest`` string that may carry a leading
    ``v``/prefix or trailing metadata.
    """
    match = re.search(r"\d+(?:\.\d+){0,2}", version.strip().lstrip("vV"))
    if match is None:
        return (0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


# Mirrors golang.org/x/mod/module.IsPseudoVersion, with the leading "v" optional.
_GO_PSEUDO_VERSION_RE = re.compile(
    r"^[vV]?\d+\.(?:0\.0-|\d+\.\d+-(?:[^+]*\.)?0\.)\d{14}-[A-Za-z0-9]+(?:\+[0-9A-Za-z-]+)?$"
)


def is_go_pseudo_version(version: str) -> bool:
    return _GO_PSEUDO_VERSION_RE.match(version.strip()) is not None


def semver_precedence(
    version: str,
) -> tuple[tuple[int, ...], bool, tuple[tuple[int, int, str], ...]]:
    """Sort key following SemVer 2.0 precedence, prerelease identifiers included.

    A Go pseudo-version is a prerelease whose identifiers end in a fixed-width
    timestamp, so SemVer precedence alone orders it by base version, then time.
    """
    core, _, pre = version.strip().lstrip("vV").split("+")[0].partition("-")
    nums = (*semver_tuple(core), 0, 0, 0)[:3]
    ids = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split(".") if pre
    )
    return nums, not pre, ids


def hold_ceiling_spec(max_version: str) -> str:
    """Return a PEP 440 upper-bound constraint for a hold ceiling."""
    parts = [p for p in max_version.strip().split(".") if p]
    padded = (parts + ["999999"] * 3)[:3]
    return "<=" + ".".join(padded)


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

    A Go pseudo-version ceiling is an exact cap with full SemVer precedence:
    its numeric prefix is shared by every later commit, so prefix semantics
    would never block one.
    """
    if is_go_pseudo_version(ceiling):
        return semver_precedence(version) <= semver_precedence(ceiling)
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
