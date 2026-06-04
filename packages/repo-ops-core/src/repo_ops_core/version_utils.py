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
