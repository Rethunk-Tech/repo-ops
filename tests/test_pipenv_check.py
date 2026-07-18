"""Tests for pipenv check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace
from repo_ops_py_pipenv.pipenv import apply_pipenv, check_pipenv


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pipenv",
        git_repo=tmp_path,
        label="demo",
    )


_OUTDATED_OUTPUT = """\
Package    Version  Latest  Type
---------- -------  ------  -----
requests   2.28.0   2.31.0  wheel
certifi    2022.12.7 2023.7.22 wheel
"""


def test_check_pipenv_parses_outdated_output(tmp_path: Path) -> None:
    """check_pipenv must parse the tabular output of pip list --outdated."""

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_OUTDATED_OUTPUT, stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    assert len(changes) == 2
    names = {c.name for c in changes}
    assert "requests" in names
    assert "certifi" in names


def test_check_pipenv_returns_empty_when_up_to_date(tmp_path: Path) -> None:
    empty_output = "Package  Version  Latest  Type\n------- -------- ------- -----\n"

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=empty_output, stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    assert changes == []


def test_check_pipenv_warns_when_pipenv_absent(tmp_path: Path, capsys) -> None:
    """check_pipenv must not raise when pipenv is absent — returns [] + WARN."""
    with patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value=None):
        result = check_pipenv(_ws(tmp_path))

    assert result == []
    assert "WARN" in capsys.readouterr().err


def test_check_pipenv_warns_when_venv_not_initialised(tmp_path: Path, capsys) -> None:
    """When 'pipenv --venv' fails (no venv), check_pipenv returns [] + WARN (no raise)."""

    def fake_run(cmd, cwd, timeout=None):
        # Probe fails — venv not yet created
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="No virtualenv has been created"
        )

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        result = check_pipenv(_ws(tmp_path))

    assert result == []
    err = capsys.readouterr().err
    assert "WARN" in err


def test_check_pipenv_raises_when_pip_fails(tmp_path: Path) -> None:
    """When the venv probe succeeds but pip list fails, check_pipenv raises RuntimeError."""
    call_count = 0

    def fake_run(cmd, cwd, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Probe succeeds — venv found at /fake/.venv
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        # pip list fails
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pip failed")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        try:
            check_pipenv(_ws(tmp_path))
        except RuntimeError as exc:
            assert "pip failed" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_check_pipenv_invokes_venv_probe_then_pip(tmp_path: Path) -> None:
    """check_pipenv probes for the venv first, then calls pip list --outdated --format=json."""
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        check_pipenv(_ws(tmp_path))

    assert len(captured) == 2, f"expected 2 calls (probe + pip), got {captured}"
    assert captured[0] == ["pipenv", "--venv"], (
        f"first call must be probe; got {captured[0]}"
    )
    pip_call = captured[1]
    assert "pip" in pip_call[0], f"second call must invoke pip; got {pip_call}"
    assert "--outdated" in pip_call
    assert "--format=json" in pip_call


def test_apply_pipenv_runs_pipenv_update(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        apply_pipenv(_ws(tmp_path))

    assert captured == [["pipenv", "update"]]


def test_apply_pipenv_raises_when_pipenv_absent(tmp_path: Path) -> None:
    with patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value=None):
        try:
            apply_pipenv(_ws(tmp_path))
        except RuntimeError as exc:
            assert "pipenv not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_check_pipenv_parses_json_outdated(tmp_path: Path) -> None:
    """F160 — JSON pip list --outdated output parses into Change entries."""
    json_out = (
        '[{"name": "requests", "version": "2.28.0", "latest_version": "2.31.0"},'
        ' {"name": "certifi", "version": "2022.12.7", "latest_version": "2023.7.22"}]'
    )

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json_out, stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    assert len(changes) == 2
    assert changes[0].name == "requests"
    assert changes[0].from_ver == "2.28.0"
    assert changes[0].to == "2.31.0"


def test_check_pipenv_json_keyerror_falls_back_without_raise(
    tmp_path: Path,
) -> None:
    """F160 — KeyError on JSON entries is caught (column fallback, no raise)."""
    # Valid JSON list but missing version keys → KeyError → column fallback.
    json_out = '[{"name": "requests"}]'

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=json_out, stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    # Column fallback cannot parse JSON text; result is empty, not an exception.
    assert changes == []


def test_check_pipenv_invalid_json_falls_back_to_columns(tmp_path: Path) -> None:
    """F160 — JSONDecodeError falls back to column-format parsing."""
    column_out = (
        "Package Version Latest\n"
        "------- ------- ------\n"
        "requests 2.28.0 2.31.0\n"
    )

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=column_out, stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].name == "requests"
    assert changes[0].to == "2.31.0"


def test_check_pipenv_venv_probe_uses_timeout_check(tmp_path: Path) -> None:
    """F212 — pipenv --venv probe uses resolve_timeout_check."""
    from repo_ops_core.constants import TIMEOUT_CHECK

    captured: list[int | None] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        check_pipenv(_ws(tmp_path))

    assert captured[0] == TIMEOUT_CHECK
