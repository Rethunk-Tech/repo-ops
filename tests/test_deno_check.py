"""Tests for Deno check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js.deno import apply_deno, check_deno
from repo_ops_models import Change

_BOX_OUTPUT = """\
┌──────────────────────┬─────────┬─────────┬─────────┐
│ Package              │ Current │ Update  │ Latest  │
├──────────────────────┼─────────┼─────────┼─────────┤
│ jsr:@std/assert      │ 0.221.0 │ 0.226.0 │ 0.226.0 │
│ npm:chalk            │ 5.2.0   │ 5.3.0   │ 5.3.0   │
└──────────────────────┴─────────┴─────────┴─────────┘
"""

_BOX_BEHIND_LATEST = """\
┌──────────────────────┬─────────┬─────────┬─────────┐
│ Package              │ Current │ Update  │ Latest  │
├──────────────────────┼─────────┼─────────┼─────────┤
│ npm:lodash           │ 4.17.20 │ 4.17.20 │ 4.17.21 │
└──────────────────────┴─────────┴─────────┴─────────┘
"""

_PLAIN_OUTPUT = """\
Package             Current  Latest
jsr:@std/assert     0.221.0  0.226.0
npm:chalk           5.2.0    5.3.0
"""

_PLAIN_4_OUTPUT = """\
Package             Current  Update   Latest
jsr:@std/assert     0.221.0  0.226.0  0.226.0
npm:chalk           5.2.0    5.2.0    5.3.0
"""


def test_check_deno_parses_box_table(tmp_path: Path, make_workspace) -> None:
    """check_deno must parse the box-drawing table format."""

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_BOX_OUTPUT, stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes, behind = check_deno(
            make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo")
        )

    assert len(changes) == 2
    assert behind == []
    names = {c.name for c in changes}
    assert "jsr:@std/assert" in names
    assert "npm:chalk" in names


def test_check_deno_latest_mode_promotes_behind(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Latest is the target and behind_latest stays empty."""

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_BOX_BEHIND_LATEST, stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes, behind = check_deno(
            make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo")
        )

    assert len(changes) == 1
    assert changes[0].to == "4.17.21"
    assert behind == []


def test_check_deno_parses_plain_table(tmp_path: Path, make_workspace) -> None:
    """check_deno must parse the plain-text column format."""

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_PLAIN_OUTPUT, stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes, behind = check_deno(
            make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo")
        )

    assert len(changes) == 2
    assert behind == []
    names = {c.name for c in changes}
    assert "jsr:@std/assert" in names
    assert "npm:chalk" in names


def test_check_deno_returns_empty_when_up_to_date(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="All packages are up to date.\n", stderr=""
        )

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        changes, behind = check_deno(
            make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo")
        )

    assert changes == []
    assert behind == []


def test_check_deno_warns_when_deno_absent(tmp_path: Path, capsys, make_workspace) -> None:
    """check_deno must not raise when deno is absent — returns [] + WARN."""
    with patch("repo_ops_js.deno.shutil.which", return_value=None):
        changes, behind = check_deno(
            make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo")
        )

    assert changes == []
    assert behind == []
    assert "WARN" in capsys.readouterr().err


def test_check_deno_raises_on_nonzero_returncode(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="deno: command failed")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        try:
            check_deno(make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo"))
        except RuntimeError as exc:
            assert "deno" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError")


def test_check_deno_invokes_deno_outdated(tmp_path: Path, make_workspace) -> None:
    """check_deno must call exactly 'deno outdated'."""
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        check_deno(make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo"))

    assert captured == [["deno", "outdated"]]


def test_apply_deno_passes_latest_flag(tmp_path: Path, monkeypatch, make_workspace) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        apply_deno(make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo"))

    assert ["deno", "outdated", "--update", "--latest"] in captured


def test_apply_deno_raises_when_deno_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_js.deno.shutil.which", return_value=None):
        try:
            apply_deno(make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo"))
        except RuntimeError as exc:
            assert "deno not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_apply_deno_calls_ghost_bump_guard(tmp_path: Path, make_workspace) -> None:
    """apply_deno verifies planned targets from the Deno lockfile."""

    def fake_run(cmd, cwd, timeout=None):
        if cmd == ["deno", "outdated"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=_BOX_OUTPUT, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
        patch(
            "repo_ops_js.deno.versions_deno_lock",
            return_value={
                "jsr:@std/assert@0.226.0",
                "npm:chalk@5.3.0",
            },
        ),
        patch("repo_ops_js.guards.resolved_version_from_node_modules", return_value=None),
    ):
        apply_deno(make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo"))


def test_apply_deno_defers_lockfile_ghost_bump_with_targeted_bump(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Targeted mode folds unresolved bulk targets into the retry list."""
    ghost = Change("jsr:@std/assert", "0.225.0", "0.226.0")

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.check_deno", return_value=([ghost], [])),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
        patch(
            "repo_ops_js.deno.versions_deno_lock",
            return_value={"jsr:@std/assert@0.225.0"},
        ),
        patch("repo_ops_js.guards.resolved_version_from_node_modules", return_value=None),
        patch(
            "repo_ops_js.deno.targeted_bump_behind",
            return_value=([], [ghost]),
        ) as targeted,
    ):
        result = apply_deno(
            make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo")
        )

    targeted.assert_called_once_with(
        make_workspace(tmp_path, ecosystem="deno", manager="deno", label="demo"),
        [ghost],
    )
    assert result == [ghost]
