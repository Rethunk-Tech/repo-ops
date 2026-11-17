"""Tests for pipenv check/apply."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_models import Change
from repo_ops_py_pipenv.pipenv import (
    _apply_pipenv_targeted_bumps,
    _pipenv_bumped_requirement,
    _pipenv_direct_dependencies,
    _pipfile_lock_versions,
    apply_pipenv,
    check_pipenv,
)


def test_check_pipenv_raises_when_pip_fails(tmp_path: Path, make_workspace) -> None:
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
            check_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))
        except RuntimeError as exc:
            assert "pip failed" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_check_pipenv_invokes_venv_probe_then_pip(
    tmp_path: Path, make_workspace, fake_run: Any
) -> None:
    """check_pipenv probes for the venv first, then calls pip list --outdated --format=json."""

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        check_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))

    assert len(fake_run.calls) == 2, f"expected 2 calls (probe + pip), got {fake_run.calls}"
    assert fake_run.calls[0] == ["pipenv", "--venv"], (
        f"first call must be probe; got {fake_run.calls[0]}"
    )
    pip_call = fake_run.calls[1]
    assert "pip" in pip_call[0], f"second call must invoke pip; got {pip_call}"
    assert "--outdated" in pip_call
    assert "--format=json" in pip_call


def test_apply_pipenv_raises_when_pipenv_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value=None):
        try:
            apply_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))
        except RuntimeError as exc:
            assert "pipenv not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_pipfile_lock_versions_skips_non_dict_and_empty(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo")
    (ws.path / "Pipfile.lock").write_text(
        json.dumps(
            {
                "default": {
                    "requests": {"version": "==2.31.0"},
                    "bad": "not-a-dict",
                    "": {"version": "==1.0.0"},
                    "empty": {"version": ""},
                },
                "develop": {"pytest": {"version": "==7.4.0"}},
            }
        )
    )
    assert _pipfile_lock_versions(ws) == {"requests": "2.31.0", "pytest": "7.4.0"}


def test_pipenv_direct_dependencies_skips_vcs_and_non_strings(tmp_path: Path) -> None:
    manifest = tmp_path / "Pipfile"
    manifest.write_text(
        "[packages]\n"
        'requests = "*"\n'
        'vcs = {git = "https://example.com/x.git"}\n'
        'local = {path = "."}\n'
        'with_ver = {version = ">=1.0"}\n'
        'no_ver = {extras = ["x"]}\n'
        "[dev-packages]\n"
        'pytest = "==7.4.0"\n'
    )
    assert _pipenv_direct_dependencies(manifest) == {
        "requests": "*",
        "with_ver": ">=1.0",
        "pytest": "==7.4.0",
    }


def test_pipenv_bumped_requirement_preserves_operator() -> None:
    assert _pipenv_bumped_requirement("^1.0.0", "2.0.0") == "^2.0.0"
    assert _pipenv_bumped_requirement(">=1.0", "2.0") == ">=2.0"
    assert _pipenv_bumped_requirement("1.0.0", "2.0.0") == "2.0.0"


def test_check_pipenv_returns_empty_when_pipenv_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value=None):
        assert check_pipenv(
            make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo")
        ) == ([], [])


def test_check_pipenv_returns_direct_changes_when_venv_missing(
    tmp_path: Path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo")
    (ws.path / "Pipfile").write_text('[packages]\nrequests = "==2.31.0"\n')
    (ws.path / "Pipfile.lock").write_text(
        json.dumps({"default": {"requests": {"version": "==2.31.0"}}, "develop": {}})
    )

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no venv")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch("repo_ops_py_pipenv.pipenv._pypi_latest", return_value="2.32.0"),
    ):
        in_range, pending = check_pipenv(ws)
    assert in_range == []
    assert pending == [Change("requests", "2.31.0", "2.32.0")]


def test_check_pipenv_parses_json_outdated(tmp_path: Path, make_workspace) -> None:
    call_count = 0

    def fake_run(cmd, cwd, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='[{"name": "requests", "version": "2.31.0", "latest_version": "2.32.0"}]',
            stderr="",
        )

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch("repo_ops_py_pipenv.pipenv._pypi_latest", return_value=None),
    ):
        in_range, pending = check_pipenv(
            make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo")
        )
    assert in_range == [Change("requests", "2.31.0", "2.32.0")]
    assert pending == []


def test_check_pipenv_parses_column_format_when_json_invalid(
    tmp_path: Path, make_workspace
) -> None:
    call_count = 0

    def fake_run(cmd, cwd, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Package Version Latest\n------- ------- ------\nrequests 2.31.0 2.32.0\nshort\n",
            stderr="",
        )

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch("repo_ops_py_pipenv.pipenv._pypi_latest", return_value=None),
    ):
        in_range, pending = check_pipenv(
            make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo")
        )
    assert in_range == [Change("requests", "2.31.0", "2.32.0")]
    assert pending == []


def test_apply_pipenv_raises_when_update_fails(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="update failed")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        try:
            apply_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))
        except RuntimeError as exc:
            assert "update failed" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_apply_pipenv_targeted_bumps_returns_candidates_when_venv_missing(
    tmp_path: Path, make_workspace
) -> None:
    candidates = [Change("requests", "2.31.0", "2.32.0")]

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no venv")

    with patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run):
        remaining = _apply_pipenv_targeted_bumps(
            make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"),
            candidates,
        )
    assert remaining == candidates


def test_apply_pipenv_targeted_bumps_skips_held_package(tmp_path: Path, make_workspace) -> None:
    candidates = [Change("requests", "2.31.0", "2.32.0")]
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch("repo_ops_py_pipenv.pipenv.target_within_hold", return_value=False),
    ):
        remaining = _apply_pipenv_targeted_bumps(
            make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"),
            candidates,
        )
    assert remaining == candidates
    assert calls == [["pipenv", "--venv"]]


def test_apply_pipenv_targeted_bumps_restores_and_reraises_install_error(
    tmp_path: Path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo")
    pipfile = ws.path / "Pipfile"
    lockfile = ws.path / "Pipfile.lock"
    pipfile.write_text('[packages]\nrequests = "==2.31.0"\n')
    lockfile.write_text(
        json.dumps({"default": {"requests": {"version": "==2.31.0"}}, "develop": {}})
    )
    original_pipfile = pipfile.read_text()
    original_lock = lockfile.read_text()

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        pipfile.write_text("mutated")
        lockfile.write_text("mutated")
        raise RuntimeError("install boom")

    with patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run):
        try:
            _apply_pipenv_targeted_bumps(ws, [Change("requests", "2.31.0", "2.32.0")])
        except RuntimeError as exc:
            assert "install boom" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
    assert pipfile.read_text() == original_pipfile
    assert lockfile.read_text() == original_lock
