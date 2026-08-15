"""Tests for PDM check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_py_pdm.pdm import apply_pdm, check_pdm


def test_check_pdm_returns_changes_from_dry_run(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """check_pdm parses '- name old -> new' lines from pdm update --dry-run."""
    monkeypatch.setenv("REPO_OPS_PDM_TARGETED_BUMP", "0")
    output = (
        "Dry run: updating packages\n"
        "  - requests 2.28.0 -> 2.31.0\n"
        "  ~ certifi 2022.12.7 -> 2023.7.22\n"
    )

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr="")

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        changes = check_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))

    assert len(changes) == 2
    names = {c.name for c in changes}
    assert "requests" in names
    assert "certifi" in names
    requests_change = next(c for c in changes if c.name == "requests")
    assert requests_change.from_ver == "2.28.0"
    assert requests_change.to == "2.31.0"


def test_check_pdm_returns_empty_when_up_to_date(tmp_path: Path, monkeypatch, make_workspace) -> None:
    monkeypatch.setenv("REPO_OPS_PDM_TARGETED_BUMP", "0")
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="All packages are up-to-date.\n", stderr=""
        )

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        changes = check_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))

    assert changes == []


def test_check_pdm_warns_when_pdm_not_on_path(tmp_path: Path, capsys, monkeypatch, make_workspace) -> None:
    """check_pdm must not raise when pdm is absent — returns [] + WARN."""
    monkeypatch.setenv("REPO_OPS_PDM_TARGETED_BUMP", "0")
    with patch("repo_ops_py_pdm.pdm.shutil.which", return_value=None):
        result = check_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))

    assert result == []
    assert "WARN" in capsys.readouterr().err


def test_check_pdm_raises_on_nonzero_returncode(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="lock file is corrupt"
        )

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        try:
            check_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))
        except RuntimeError as exc:
            assert "lock file is corrupt" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_apply_pdm_runs_pdm_update(tmp_path: Path, make_workspace) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        apply_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))

    assert captured == [["pdm", "update"]]


def test_apply_pdm_raises_when_pdm_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_py_pdm.pdm.shutil.which", return_value=None):
        try:
            apply_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))
        except RuntimeError as exc:
            assert "pdm not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
