"""Tests for PDM targeted constraint bumps."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change
from repo_ops_py_pdm.pdm import (
    _pdm_requirement_satisfied,
    apply_pdm,
    check_pdm,
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


def test_pdm_bare_version_requirement_is_caret_compatible() -> None:
    assert _pdm_requirement_satisfied("2.31.0", "2.28.0") is True
    assert _pdm_requirement_satisfied("3.0.0", "2.28.0") is False
    assert _pdm_requirement_satisfied("2.31.0", "==2.28.0") is False


def test_check_pdm_reports_latest_direct_dependency(
    tmp_path: Path, make_workspace
) -> None:
    _write_fixture(tmp_path)
    with (
        patch.dict(os.environ, {"REPO_OPS_PDM_TARGETED_BUMP": "1"}),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=_fake_pdm_run),
        patch("repo_ops_py_pdm.pdm._pdm_latest_version", return_value="3.0.0"),
    ):
        result = check_pdm(
            make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo")
        )

    assert result == (
        [],
        [Change("requests", "2.31.0", "3.0.0")],
    )


def test_apply_pdm_restores_files_when_targeted_add_fails(
    tmp_path: Path, make_workspace
) -> None:
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
        remaining = apply_pdm(
            make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo")
        )

    assert remaining == [Change("requests", "2.31.0", "3.0.0")]
    assert manifest.read_bytes() == original_manifest
    assert lockfile.read_bytes() == original_lockfile


def test_apply_pdm_adds_targeted_constraint(tmp_path: Path, make_workspace) -> None:
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
        remaining = apply_pdm(
            make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo")
        )

    assert remaining == []
    assert captured == [["pdm", "update"], ["pdm", "add", "requests@^3.0.0"]]


def test_apply_pdm_restores_files_when_targeted_add_lands_wrong_version(
    tmp_path: Path,
    make_workspace,
) -> None:
    _write_fixture(tmp_path)
    manifest = tmp_path / "pyproject.toml"
    lockfile = tmp_path / "pdm.lock"
    original_manifest = manifest.read_bytes()
    original_lockfile = lockfile.read_bytes()

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pdm", "add"]:
            lockfile.write_text(
                '[[package]]\nname = "requests"\nversion = "2.32.0"\n',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch.dict(os.environ, {"REPO_OPS_PDM_TARGETED_BUMP": "1"}),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
        patch("repo_ops_py_pdm.pdm._pdm_latest_version", return_value="3.0.0"),
    ):
        remaining = apply_pdm(
            make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo")
        )

    assert remaining == [Change("requests", "2.31.0", "3.0.0")]
    assert manifest.read_bytes() == original_manifest
    assert lockfile.read_bytes() == original_lockfile
