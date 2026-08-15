"""Fixed-width column formatting for human-readable CLI tables."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

Align = Literal["left", "right"]


def column_width(values: Iterable[str], header: str, *, cap: int | None = None) -> int:
    """Minimum width for a column from header label and cell values."""
    width = len(header)
    for value in values:
        width = max(width, len(value))
    if cap is not None:
        width = min(width, cap)
    return width


def truncate_cell(text: str, width: int) -> str:
    """Truncate text to width, using a single ellipsis when needed."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def format_cell(text: str, width: int, *, align: Align = "left") -> str:
    """Pad or truncate text to an exact column width."""
    cell = truncate_cell(text, width)
    if align == "right":
        return cell.rjust(width)
    return cell.ljust(width)


def join_row(cells: list[str], *, sep: str = "  ") -> str:
    """Join pre-formatted cells with a separator (no extra padding)."""
    return sep.join(cells)
