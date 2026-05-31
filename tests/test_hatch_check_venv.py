"""check_hatch must not create .dep_refresh_venv in the workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace
from repo_ops_py_hatch.hatch import check_hatch


def test_check_hatch_uses_temp_venv_not_workspace_dir(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    legacy = tmp_path / ".dep_refresh_venv"
    legacy.mkdir()
    marker = legacy / "keep-me"
    marker.write_text("preserve", encoding="utf-8")

    ws = Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="hatch",
        git_repo=tmp_path,
        label="",
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            assert venv.parent != tmp_path
            venv.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert marker.exists()
    assert legacy.is_dir()
