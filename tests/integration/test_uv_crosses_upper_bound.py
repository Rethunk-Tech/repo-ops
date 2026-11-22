"""Integration: a ``<N`` manifest cap is raised to the newest release, like pdm.

Wheels come from a local find-links directory with the index disabled; only the
PyPI latest lookup is stubbed.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_models import Change, Workspace
from repo_ops_py.uv_lock import apply_uv_lock, check_uv_lock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH"),
]


def _wheel(dest: Path, version: str) -> None:
    info = f"fxpkg-{version}.dist-info"
    with zipfile.ZipFile(dest / f"fxpkg-{version}-py3-none-any.whl", "w") as zf:
        zf.writestr("fxpkg/__init__.py", "")
        zf.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: fxpkg\nVersion: {version}\n")
        zf.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr(f"{info}/RECORD", "")


def test_plan_lists_and_apply_crosses_the_upper_bound(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel(wheels, "1.0")
    _wheel(wheels, "2.0")
    proj = tmp_path / "proj"
    proj.mkdir()
    manifest = proj / "pyproject.toml"
    manifest.write_text(
        '[project]\nname = "fx"\nversion = "0"\nrequires-python = ">=3.12"\n'
        'dependencies = ["fxpkg>=1.0,<2"]\n\n'
        '[tool.uv]\nfind-links = ["../wheels"]\nno-index = true\npackage = false\n'
    )
    subprocess.run(["uv", "lock", "-q"], cwd=proj, check=True, capture_output=True)
    ws = Workspace(path=proj, ecosystem="py", manager="uv", git_repo=proj, label=".")
    before = manifest.read_text()

    with patch("repo_ops_py.direct_requirements._pypi_latest", return_value="2.0"):
        changes, blocked = check_uv_lock(ws)
        assert changes == [Change("fxpkg", "1.0", "2.0")]
        assert blocked == []
        assert manifest.read_text() == before
        assert apply_uv_lock(ws) == []

    assert "fxpkg>=2.0" in manifest.read_text()
    assert 'version = "2.0"' in (proj / "uv.lock").read_text()
