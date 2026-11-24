"""One-line failure summaries from multi-line tool output."""

from __future__ import annotations

import re

_ERROR_LINE = re.compile(r"\S+:\d+(?::\d+)?\b|\b(?:error|FAIL|panic|failed)\b|[\u00d7\u2717]", re.I)
_ERROR_PREFIX = re.compile(r"error:", re.I)


def first_error_line(text: str | None) -> str:
    """The line that names the cause: the first ``error:`` line, else the first error-like line.

    The first line is skipped in the fallback because it is usually a header (gate's step
    line) or tool noise (bun's env-file load line).
    """
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    for line in lines:
        if _ERROR_PREFIX.match(line):
            return line
    for line in lines[1:]:
        if _ERROR_LINE.search(line):
            return line
    return lines[0]
