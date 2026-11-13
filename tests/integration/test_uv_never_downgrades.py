"""Integration: ``uv lock --upgrade`` lowering a locked version fails the apply.

When the locked release leaves the index (yanked, or a find-links wheel
removed), uv resolves the next lower one. Wheels come from a local find-links
directory with the index disabled, so no network is touched.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from repo_ops_models import Workspace
from repo_ops_py.uv_lock import apply_uv_lock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH"),
]


def _wheel(dest: Path, version: str) -> Path:
    path = dest / f"fxpkg-{version}-py3-none-any.whl"
    info = f"fxpkg-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fxpkg/__init__.py", "")
        zf.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: fxpkg\nVersion: {version}\n")
        zf.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr(f"{info}/RECORD", "")
    return path


def test_a_lowered_lock_version_fails_the_apply(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel(wheels, "1.0")
    newest = _wheel(wheels, "1.1")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "fx"\nversion = "0"\nrequires-python = ">=3.12"\n'
        'dependencies = ["fxpkg>=1.0"]\n\n'
        '[tool.uv]\nfind-links = ["../wheels"]\nno-index = true\npackage = false\n'
    )
    subprocess.run(["uv", "lock", "-q"], cwd=proj, check=True, capture_output=True)
    assert 'version = "1.1"' in (proj / "uv.lock").read_text()
    newest.unlink()

    with pytest.raises(RuntimeError, match=r"fxpkg 1\.1 -> 1\.0"):
        apply_uv_lock(Workspace(path=proj, ecosystem="py", manager="uv", git_repo=proj, label="."))
