"""Lightweight tests for dep_refresh pure helpers and safety regressions.

Monkeypatches subprocess via the run helper.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

# Import from the command module under test
from repo_ops.commands.dep_refresh import (
    _parse_requirements_lines,
    normalize_version,
    semver_tuple,
)


def test_normalize_version_strips_prefixes():
    assert normalize_version("^1.2.3") == "1.2.3"
    assert normalize_version("~>2.0") == "2.0"
    assert normalize_version(">=1.0.0-beta") == "1.0.0"


def test_semver_tuple_parses():
    assert semver_tuple("1.2.3") == (1, 2, 3)
    assert semver_tuple("^4.5") == (4, 5)


def test_parse_requirements_lines_basic():
    text = "foo==1.0\nbar==2.0\n# comment\n-baz==3"
    assert _parse_requirements_lines(text) == {"foo": "1.0", "bar": "2.0"}


def test_checkers_use_monkeypatched_run(monkeypatch):
    """Safety regression: ensure checkers can be exercised with mocked pm calls."""
    from repo_ops.commands.dep_refresh import Workspace, check_bun

    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Package  current  latest\nfoo 1.0 latest 1.1\n", stderr=""
        )

    ws = Workspace(
        path=Path("/tmp"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp"),
        label="",
    )
    with patch("repo_ops.commands.dep_refresh.run", fake_run):
        changes = check_bun(ws)
    assert len(changes) == 1
    assert changes[0].name == "foo"
    assert any("bun" in c[0] for c in calls)
