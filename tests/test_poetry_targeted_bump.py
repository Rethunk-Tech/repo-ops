from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change
from repo_ops_py_poetry.poetry import (
    _poetry_requirement_satisfied,
    apply_poetry,
    check_poetry,
)


def _seed_workspace(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[tool.poetry.dependencies]\nrequests = "^2.28.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n',
        encoding="utf-8",
    )


def test_poetry_bare_version_uses_caret_range() -> None:
    assert _poetry_requirement_satisfied("2.31.0", "2.28.0") is True
    assert _poetry_requirement_satisfied("3.0.0", "2.28.0") is False
    assert _poetry_requirement_satisfied("2.31.0", "==2.28.0") is False


def test_check_poetry_reports_constraint_bump(tmp_path: Path, make_workspace) -> None:
    _seed_workspace(tmp_path)

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        patch(
            "repo_ops_py_poetry.poetry._poetry_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_poetry.poetry._pypi_latest",
            return_value="3.0.0",
        ),
    ):
        result = check_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    in_range, behind = result
    assert in_range == []
    assert behind == [Change("requests", "2.31.0", "3.0.0")]


def test_apply_poetry_restores_failed_targeted_add(tmp_path: Path, make_workspace) -> None:
    _seed_workspace(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "poetry.lock"
    manifest_before = manifest.read_bytes()
    lock_before = lockfile.read_bytes()
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-1].startswith("requests@"):
            manifest.write_text("changed\n", encoding="utf-8")
            lockfile.write_text("changed\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        patch(
            "repo_ops_py_poetry.poetry._poetry_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_poetry.poetry._poetry_latest_direct_changes",
            return_value=[Change("requests", "2.31.0", "3.0.0")],
        ),
    ):
        remaining = apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    assert remaining == [Change("requests", "2.31.0", "3.0.0")]
    assert manifest.read_bytes() == manifest_before
    assert lockfile.read_bytes() == lock_before
    assert calls == [["poetry", "update"], ["poetry", "add", "requests@^3.0.0"]]


def test_apply_poetry_restores_when_target_version_does_not_land(
    tmp_path: Path, make_workspace,
) -> None:
    _seed_workspace(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "poetry.lock"
    manifest_before = manifest.read_bytes()
    lock_before = lockfile.read_bytes()

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[-1].startswith("requests@"):
            manifest.write_text("changed\n", encoding="utf-8")
            lockfile.write_text(
                '[[package]]\nname = "requests"\nversion = "2.31.1"\n',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        patch(
            "repo_ops_py_poetry.poetry._poetry_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_poetry.poetry._poetry_latest_direct_changes",
            return_value=[Change("requests", "2.31.0", "3.0.0")],
        ),
    ):
        remaining = apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    assert remaining == [Change("requests", "2.31.0", "3.0.0")]
    assert manifest.read_bytes() == manifest_before
    assert lockfile.read_bytes() == lock_before


def test_apply_poetry_keeps_successful_targeted_add(tmp_path: Path, make_workspace) -> None:
    _seed_workspace(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "poetry.lock"
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-1].startswith("requests@"):
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + "# updated\n",
                encoding="utf-8",
            )
            lockfile.write_text(
                '[[package]]\nname = "requests"\nversion = "3.0.0"\n',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        patch(
            "repo_ops_py_poetry.poetry._poetry_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_poetry.poetry._poetry_latest_direct_changes",
            return_value=[Change("requests", "2.31.0", "3.0.0")],
        ),
    ):
        remaining = apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    assert remaining == []
    assert "updated" in manifest.read_text(encoding="utf-8")
    assert "3.0.0" in lockfile.read_text(encoding="utf-8")
    assert calls == [["poetry", "update"], ["poetry", "add", "requests@^3.0.0"]]
