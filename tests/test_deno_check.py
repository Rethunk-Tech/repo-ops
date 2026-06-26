"""Tests for Deno check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js.deno import apply_deno, check_deno
from repo_ops_models import Workspace


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="deno",
        manager="deno",
        git_repo=tmp_path,
        label="demo",
    )


_BOX_OUTPUT = """\
┌──────────────────────┬─────────┬─────────┬─────────┐
│ Package              │ Current │ Update  │ Latest  │
├──────────────────────┼─────────┼─────────┼─────────┤
│ jsr:@std/assert      │ 0.221.0 │ 0.226.0 │ 0.226.0 │
│ npm:chalk            │ 5.2.0   │ 5.3.0   │ 5.3.0   │
└──────────────────────┴─────────┴─────────┴─────────┘
"""

_PLAIN_OUTPUT = """\
Package             Current  Latest
jsr:@std/assert     0.221.0  0.226.0
npm:chalk           5.2.0    5.3.0
"""


def test_check_deno_parses_box_table(tmp_path: Path) -> None:
    """check_deno must parse the box-drawing table format."""
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_BOX_OUTPUT, stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes = check_deno(_ws(tmp_path))

    assert len(changes) == 2
    names = {c.name for c in changes}
    assert "jsr:@std/assert" in names
    assert "npm:chalk" in names


def test_check_deno_parses_plain_table(tmp_path: Path) -> None:
    """check_deno must parse the plain-text column format."""
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_PLAIN_OUTPUT, stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes = check_deno(_ws(tmp_path))

    assert len(changes) == 2
    names = {c.name for c in changes}
    assert "jsr:@std/assert" in names
    assert "npm:chalk" in names


def test_check_deno_returns_empty_when_up_to_date(tmp_path: Path) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="All packages are up to date.\n", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes = check_deno(_ws(tmp_path))

    assert changes == []


def test_check_deno_warns_when_deno_absent(tmp_path: Path, capsys) -> None:
    """check_deno must not raise when deno is absent — returns [] + WARN."""
    with patch("repo_ops_js.deno.shutil.which", return_value=None):
        result = check_deno(_ws(tmp_path))

    assert result == []
    assert "WARN" in capsys.readouterr().err


def test_check_deno_raises_on_nonzero_returncode(tmp_path: Path) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="deno: command failed")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        try:
            check_deno(_ws(tmp_path))
        except RuntimeError as exc:
            assert "deno" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError")


def test_check_deno_invokes_deno_outdated(tmp_path: Path) -> None:
    """check_deno must call exactly 'deno outdated'."""
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        check_deno(_ws(tmp_path))

    assert captured == [["deno", "outdated"]]


def test_apply_deno_runs_deno_outdated_update(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        apply_deno(_ws(tmp_path))

    assert captured == [["deno", "outdated", "--update"]]


def test_apply_deno_raises_when_deno_absent(tmp_path: Path) -> None:
    with patch("repo_ops_js.deno.shutil.which", return_value=None):
        try:
            apply_deno(_ws(tmp_path))
        except RuntimeError as exc:
            assert "deno not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
