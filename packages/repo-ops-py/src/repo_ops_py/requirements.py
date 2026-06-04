"""Shared requirements.txt parsing helpers."""

from __future__ import annotations

import re


def parse_requirements_lines(text: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^;\s]+)", line)
        if m:
            versions[m.group(1).lower().replace("_", "-")] = m.group(2)
    return versions
