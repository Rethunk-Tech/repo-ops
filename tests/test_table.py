"""Tests for shared CLI table formatting."""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from pathlib import Path

from repo_ops_core.table import column_width, format_cell
from repo_ops_inventory.counting import scan
from repo_ops_inventory.scan import _print_inventory_table


def test_column_width_includes_header():
    assert column_width(["ab", "xyz"], "project") == len("project")


def test_format_cell_right_align_and_truncate():
    assert format_cell("42", 5, align="right") == "   42"
    assert format_cell("toolong", 4) == "too…"


def test_scan_flat_dot_uses_resolved_name(tmp_path: Path, monkeypatch):
    """Path('.').name is empty on Python 3.12+; flat scan must still label the project."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    rows = scan(Path("."), flat=True)
    assert len(rows) == 1
    assert rows[0]["name"] == tmp_path.name


def test_inventory_table_aligns_columns():
    rows = [
        {
            "name": "repo-ops",
            "kinds_s": "py(pyproject+uv)",
            "manifests": 1,
            "direct": 4,
            "versions_n": 18,
            "installed": 0,
            "edges": 26,
        }
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_inventory_table(rows)
    out = buf.getvalue()
    header, sep, data, _sep2, total = out.strip().splitlines()
    assert len(sep) == len(header)
    assert re.match(r"^project\s+kinds\s+mans\s+direct\s+versions\s+on-disk\s+edges$", header)
    assert data.startswith("repo-ops")
    assert "py(pyproject+uv)" in data
    assert total.startswith("TOTAL")
