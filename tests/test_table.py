"""Tests for shared CLI table formatting."""

from __future__ import annotations

from repo_ops_core.table import column_width, format_cell


def test_column_width_includes_header():
    assert column_width(["ab", "xyz"], "project") == len("project")


def test_format_cell_right_align_and_truncate():
    assert format_cell("42", 5, align="right") == "   42"
    assert format_cell("toolong", 4) == "too…"
