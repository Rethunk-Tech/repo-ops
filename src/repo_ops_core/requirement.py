"""Shared caret/range matching for targeted-bump constraint checks.

PEP 440 ``SpecifierSet`` does not parse Poetry/PDM/Cargo caret (``^``) or a
bare version with an implied operator. Cargo reqs are Cargo semver, not PEP
440, so this helper is the single matcher instead of ``packaging``.

Default-operator contract:

- ``default_operator="^"`` — Poetry, PDM, Cargo (bare version is caret)
- ``default_operator="=="`` — Pipenv, pip-tools (bare version is a pin)
"""

from __future__ import annotations

import re

from repo_ops_core.version_utils import version_parts

_CONSTRAINT = re.compile(
    r"^(~=|<=|>=|==|!=|<|>|=|~|\^)?\s*(\d+(?:\.\d+){0,2}|\*)",
)


def _caret_upper_bound(parts: tuple[int, int, int]) -> tuple[int, int, int]:
    major, minor, patch = parts
    if major:
        return (major + 1, 0, 0)
    if minor:
        return (0, minor + 1, 0)
    return (0, 0, patch + 1)


def _normalize_requirement(requirement: str) -> str:
    text = requirement.split(";", 1)[0].strip()
    text = re.sub(r"^\[[^\]]+\]", "", text).strip()
    return text


def requirement_satisfied(
    version: str,
    requirement: str,
    *,
    default_operator: str,
) -> bool:
    """Return whether *version* matches *requirement* under *default_operator*."""
    if not requirement or requirement.strip() == "*":
        return True
    candidate = version_parts(version)
    normalized = _normalize_requirement(requirement)
    if not normalized or normalized == "*":
        return True
    for alternative in normalized.split("||"):
        constraints = [part.strip() for part in alternative.split(",") if part.strip()]
        if not constraints:
            return True
        satisfied = True
        for constraint in constraints:
            match = _CONSTRAINT.match(constraint)
            if match is None:
                satisfied = False
                break
            operator = match.group(1) or default_operator
            raw_version = match.group(2)
            if raw_version == "*":
                continue
            parts = version_parts(raw_version)
            if operator == "^":
                if not (candidate >= parts and candidate < _caret_upper_bound(parts)):
                    satisfied = False
                    break
            elif operator in ("~", "~="):
                if not (candidate >= parts and candidate < (parts[0], parts[1] + 1, 0)):
                    satisfied = False
                    break
            elif operator in ("=", "=="):
                if candidate != parts:
                    satisfied = False
                    break
            elif operator == "!=":
                if candidate == parts:
                    satisfied = False
                    break
            elif (
                (operator == ">=" and candidate < parts)
                or (operator == ">" and candidate <= parts)
                or (operator == "<=" and candidate > parts)
                or (operator == "<" and candidate >= parts)
            ):
                satisfied = False
                break
        if satisfied:
            return True
    return False
