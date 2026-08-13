"""Tests for PDM targeted constraint bumps."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change, Workspace
from repo_ops_py_pdm.pdm import apply_pdm, check_pdm


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pdm",
        git_repo=tmp_path,
        label="demo",
    )


def _write_fixture(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests ^2.28.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n',
        encoding="utf-8",
    )


def _fake_pdm_run(cmd, cwd, timeout=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_check_pdm_reports_latest_direct_dependency(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    with (
        patch.dict(os.environ, {"REPO_OPS_PDM_TARGETED_BUMP": "1"}),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=_fake_pdm_run),
        patch("repo_ops_py_pdm.pdm._pdm_latest_version", return_value="3.0.0"),
    ):
        result = check_pdm(_ws(tmp_path))

    assert result == (
        [],
        [Change("requests", "^2.28.0", "3.0.0")],
    )


def test_apply_pdm_restores_files_when_targeted_add_fails(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "pdm.lock"
    original_manifest = manifest.read_bytes()
    original_lockfile = lockfile.read_bytes()

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pdm", "add"]:
            manifest.write_text("changed", encoding="utf-8")
            lockfile.write_text("changed", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch.dict(os.environ, {"REPO_OPS_PDM_TARGETED_BUMP": "1"}),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
        patch("repo_ops_py_pdm.pdm._pdm_latest_version", return_value="3.0.0"),
    ):
        remaining = apply_pdm(_ws(tmp_path))

    assert remaining == [Change("requests", "^2.28.0", "3.0.0")]
    assert manifest.read_bytes() == original_manifest
    assert lockfile.read_bytes() == original_lockfile


def test_apply_pdm_adds_targeted_constraint(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        if cmd[:2] == ["pdm", "add"]:
            (tmp_path / "pdm.lock").write_text(
                '[[package]]\nname = "requests"\nversion = "3.0.0"\n',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch.dict(os.environ, {"REPO_OPS_PDM_TARGETED_BUMP": "1"}),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
        patch("repo_ops_py_pdm.pdm._pdm_latest_version", return_value="3.0.0"),
    ):
        remaining = apply_pdm(_ws(tmp_path))

    assert remaining == []
    assert captured == [["pdm", "update"], ["pdm", "add", "requests@^3.0.0"]]
