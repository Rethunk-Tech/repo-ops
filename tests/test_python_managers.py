"""Python manager detection and fail-closed audit coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_audit.audit import (
    WorkspaceResult,
    _audit_py_exported,
    _detect_workspaces,
    _exit_code_for,
    audit_workspace,
    main,
)


def test_detects_python_managers_from_lockfiles_and_manifests(tmp_path: Path) -> None:
    fixtures = {
        "uv": (["pyproject.toml", "uv.lock"], "[project]\nname = 'uv-fixture'\n"),
        "poetry": (["pyproject.toml", "poetry.lock"], ""),
        "pdm": (["pyproject.toml", "pdm.lock"], ""),
        "pipenv": (["Pipfile", "Pipfile.lock"], ""),
        "pip-tools": (["requirements.in", "requirements.txt"], ""),
        "hatch": (
            ["pyproject.toml"],
            (
                "[build-system]\n"
                'requires = ["hatchling"]\n'
                'build-backend = "hatchling.build"\n'
            ),
        ),
    }
    for name, (files, pyproject) in fixtures.items():
        workspace = tmp_path / name
        workspace.mkdir()
        for filename in files:
            path = workspace / filename
            if filename == "pyproject.toml":
                path.write_text(pyproject, encoding="utf-8")
            else:
                path.touch()

    detected = {
        Path(path).name: (ecosystem, manager)
        for path, ecosystem, manager in _detect_workspaces(str(tmp_path))
    }

    assert detected == {
        "uv": ("py-uv", "uv"),
        "poetry": ("py-poetry", "poetry"),
        "pdm": ("py-pdm", "pdm"),
        "pipenv": ("py-pipenv", "pipenv"),
        "pip-tools": ("py-pip-tools", "pip-tools"),
        "hatch": ("py-hatch", "hatch"),
    }


def test_uv_lock_takes_precedence_over_hatchling(tmp_path: Path) -> None:
    workspace = tmp_path / "uv-hatch"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        (
            "[build-system]\n"
            'requires = ["hatchling"]\n'
            'build-backend = "hatchling.build"\n'
        ),
        encoding="utf-8",
    )
    (workspace / "uv.lock").touch()

    detected = _detect_workspaces(str(tmp_path))

    assert detected == [(str(workspace), "py-uv", "uv")]


def test_unknown_python_manifest_is_not_detected(tmp_path: Path) -> None:
    workspace = tmp_path / "plain"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text(
        "[project]\nname = 'plain-fixture'\n", encoding="utf-8"
    )

    assert _detect_workspaces(str(tmp_path)) == []


def test_python_export_failure_is_error_and_fails_exit() -> None:
    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/poetry"),
        patch(
            "repo_ops_audit.audit._run_python_export",
            return_value=(1, "", "export failed"),
        ),
    ):
        result = _audit_py_exported("/some/repo", "py-poetry", "poetry")

    assert result.status == "error"
    assert result.error == "poetry export failed: export failed"
    assert _exit_code_for([result], "never") == 1


@pytest.mark.parametrize(
    ("ecosystem", "manager", "skip_reason"),
    [
        ("py-uv", "uv", "uv not on PATH"),
        ("py-poetry", "poetry", "poetry not on PATH"),
        ("py-pdm", "pdm", "pdm not on PATH"),
        ("py-pipenv", "pipenv", "pipenv not on PATH"),
        ("py-hatch", "hatch", "hatch not on PATH"),
        ("py-pip-tools", "pip-tools", "pip-audit not on PATH"),
    ],
)
def test_missing_python_tools_are_visible_in_json(
    ecosystem: str,
    manager: str,
    skip_reason: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("repo_ops_audit.audit.shutil.which", return_value=None):
        result = audit_workspace(str(tmp_path), ecosystem, manager)
    assert isinstance(result, WorkspaceResult)
    assert result.status == "skipped"
    assert result.skip_reason == skip_reason

    with patch("repo_ops_audit.audit.run_audit", return_value=[result]):
        assert main(["--json", "--fail-on", "never", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    workspace = payload["workspaces"][0]
    assert workspace["status"] == "skipped"
    assert workspace["ecosystem"] == ecosystem
    assert workspace["manager"] == manager
    assert workspace["skip_reason"] == skip_reason
