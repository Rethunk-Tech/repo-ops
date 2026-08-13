from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change, Workspace
from repo_ops_py_pipenv.pipenv import (
    _apply_pipenv_targeted_bumps,
    _pipenv_requirement_satisfied,
    apply_pipenv,
    check_pipenv,
)


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pipenv",
        git_repo=tmp_path,
        label="demo",
    )


def _seed_workspace(tmp_path: Path) -> None:
    (tmp_path / "Pipfile").write_text(
        '[packages]\nrequests = "^2.28.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "Pipfile.lock").write_text(
        '{"default": {"requests": {"version": "==2.31.0"}}, "develop": {}}',
        encoding="utf-8",
    )


def test_pipenv_requirement_preserves_caret_semantics() -> None:
    assert _pipenv_requirement_satisfied("2.31.0", "^2.28.0") is True
    assert _pipenv_requirement_satisfied("3.0.0", "^2.28.0") is False
    assert _pipenv_requirement_satisfied("2.31.0", "==2.28.0") is False


def test_check_pipenv_reports_direct_constraint_bump(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pipenv.pipenv._pypi_latest",
            return_value="3.0.0",
        ),
    ):
        result = check_pipenv(_ws(tmp_path))

    in_range, behind = result
    assert in_range == []
    assert behind == [Change("requests", "2.31.0", "3.0.0")]


def test_apply_pipenv_skips_targeted_install_without_venv(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    calls: list[list[str]] = []
    candidate = Change("requests", "2.31.0", "3.0.0")

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no venv")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_latest_direct_changes",
            return_value=[candidate],
        ),
    ):
        remaining = apply_pipenv(_ws(tmp_path))

    assert remaining == [candidate]
    assert calls == [["pipenv", "update"], ["pipenv", "--venv"]]


def test_targeted_pipenv_bump_skips_missing_pipfile_constraint(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    candidate = Change("flask", "2.3.0", "3.0.0")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")

    with patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run):
        remaining = _apply_pipenv_targeted_bumps(_ws(tmp_path), [candidate])

    assert remaining == [candidate]
    assert calls == [["pipenv", "--venv"]]


def test_apply_pipenv_restores_failed_targeted_install(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    manifest = tmp_path / "Pipfile"
    lockfile = tmp_path / "Pipfile.lock"
    manifest_before = manifest.read_bytes()
    lock_before = lockfile.read_bytes()
    candidate = Change("requests", "2.31.0", "3.0.0")

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "--venv"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        if cmd[:2] == ["pipenv", "install"]:
            manifest.write_text("changed\n", encoding="utf-8")
            lockfile.write_text("changed\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_latest_direct_changes",
            return_value=[candidate],
        ),
    ):
        remaining = apply_pipenv(_ws(tmp_path))

    assert remaining == [candidate]
    assert manifest.read_bytes() == manifest_before
    assert lockfile.read_bytes() == lock_before


def test_apply_pipenv_keeps_successful_targeted_install(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    candidate = Change("requests", "2.31.0", "3.0.0")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        if cmd[:2] == ["pipenv", "install"]:
            (tmp_path / "Pipfile.lock").write_text(
                '{"default": {"requests": {"version": "==3.0.0"}}, "develop": {}}',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pipenv.pipenv._pipenv_latest_direct_changes",
            return_value=[candidate],
        ),
    ):
        remaining = apply_pipenv(_ws(tmp_path))

    assert remaining == []
    assert calls == [
        ["pipenv", "update"],
        ["pipenv", "--venv"],
        ["pipenv", "install", "requests@^3.0.0"],
    ]
