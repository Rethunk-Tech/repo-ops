"""Tests for pip-tools check/apply (#54 — check must use --upgrade)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace
from repo_ops_py_pip_tools.pip_tools import check_pip_tools


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="python",
        manager="pip-tools",
        git_repo=tmp_path,
        label="demo",
    )


def test_check_pip_tools_uv_path_uses_upgrade(tmp_path: Path) -> None:
    """#54 — uv pip compile in check must include --upgrade so proposed reflects apply."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n")

    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(cmd)
        # Return a minimal compiled output
        return type(
            "Proc",
            (),
            {"returncode": 0, "stdout": "requests==2.31.0\n", "stderr": ""},
        )()

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        check_pip_tools(_ws(tmp_path))

    assert len(captured) == 1
    assert "--upgrade" in captured[0]


def test_check_pip_tools_pip_compile_fallback_dry_run_uses_upgrade(
    tmp_path: Path,
) -> None:
    """#73 — pip-compile fallback dry-run in check must include --upgrade so plan matches apply."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n")

    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(cmd)
        return type(
            "Proc",
            (),
            {"returncode": 0, "stdout": "requests==2.31.0\n", "stderr": ""},
        )()

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: None if name == "uv" else "/usr/bin/pip-compile",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        check_pip_tools(_ws(tmp_path))

    assert len(captured) == 1
    assert "--upgrade" in captured[0]
