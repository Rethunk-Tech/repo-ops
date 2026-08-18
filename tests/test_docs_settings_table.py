"""The settings table in docs/usage.md must match SETTINGS_TABLE.

The doc table was hand-maintained and had drifted: two settings missing, one
listed that no longer existed, and 42 values disagreeing with the code. A doc
that confidently states a wrong default is worse than no doc, so the agreement
is asserted rather than trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.config import SETTINGS_TABLE

_USAGE = Path(__file__).resolve().parents[1] / "docs" / "usage.md"
_ROW = re.compile(r"^\| `(?P<name>\w+)` \| (?P<flag>.+?) \| `(?P<default>.*?)` \|$")

#: Settings sharing the --conservative flag are described in prose, not rows —
#: 22 identical rows carry no information the sentence above them does not.
CONSERVATIVE = frozenset(r[0] for r in SETTINGS_TABLE if r[1] == "--conservative")


def _doc_rows() -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for line in _USAGE.read_text(encoding="utf-8").splitlines():
        matched = _ROW.match(line.strip())
        if matched is None:
            continue
        flag = matched["flag"].strip().strip("`")
        rows[matched["name"]] = ("" if flag == "—" else flag, matched["default"])
    return rows


def test_documented_settings_match_code() -> None:
    expected = {r[0]: (r[1] or "", str(r[4])) for r in SETTINGS_TABLE if r[0] not in CONSERVATIVE}

    assert _doc_rows() == expected


def test_conservative_family_size_is_stated_correctly() -> None:
    """The prose names a count; a new --conservative setting must update it."""
    text = _USAGE.read_text(encoding="utf-8")

    assert f"{len(CONSERVATIVE)} settings share one flag" in text
