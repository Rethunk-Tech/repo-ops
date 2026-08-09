"""Python manager detection and fail-closed audit coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_audit.audit import (
    WorkspaceResult,
    _detect_workspaces,
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
