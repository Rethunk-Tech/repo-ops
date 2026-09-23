"""Integration: a module under an enclosing go.work is upgraded against its own go.mod.

Workspace-mode MVS already selects the newest version any member requires, so
``go list -m -u`` inside the workspace reports nothing for a dependency a
sibling has bumped even though this module's go.mod still records the old one.
The dependency is served from a ``file://`` GOPROXY, so no network is touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from repo_ops_go.go import apply_go, check_go
from repo_ops_models import Change, Workspace

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("go") is None, reason="go not on PATH"),
]

_DEP = "example.com/dep"


def _publish(proxy: Path, version: str) -> None:
    vdir = proxy / _DEP / "@v"
    vdir.mkdir(parents=True, exist_ok=True)
    gomod = f"module {_DEP}\n\ngo 1.22\n"
    (vdir / f"{version}.mod").write_text(gomod, encoding="utf-8")
    (vdir / f"{version}.info").write_text(
        f'{{"Version":"{version}","Time":"2026-01-01T00:00:00Z"}}', encoding="utf-8"
    )
    with zipfile.ZipFile(vdir / f"{version}.zip", "w") as zf:
        zf.writestr(f"{_DEP}@{version}/go.mod", gomod)
        zf.writestr(f"{_DEP}@{version}/dep.go", "package dep\n\nconst V = 1\n")
    with (vdir / "list").open("a", encoding="utf-8") as fh:
        fh.write(f"{version}\n")


def _member(root: Path, name: str, dep_version: str) -> Path:
    mdir = root / name
    mdir.mkdir()
    (mdir / "go.mod").write_text(
        f"module example.com/{name}\n\ngo 1.22\n\nrequire {_DEP} {dep_version}\n",
        encoding="utf-8",
    )
    (mdir / f"{name}.go").write_text(
        f'package {name}\n\nimport "{_DEP}"\n\nconst V = dep.V\n', encoding="utf-8"
    )
    subprocess.run(
        ["go", "mod", "tidy"],
        cwd=mdir,
        check=True,
        capture_output=True,
        env={**os.environ, "GOWORK": "off"},
    )
    return mdir


@pytest.fixture
def workspace_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    proxy = tmp_path / "proxy"
    _publish(proxy, "v1.0.0")
    _publish(proxy, "v1.1.0")
    monkeypatch.setenv("GOPROXY", proxy.as_uri())
    monkeypatch.setenv("GOSUMDB", "off")
    monkeypatch.setenv("GOTOOLCHAIN", "local")
    monkeypatch.setenv("GOFLAGS", "-modcacherw")
    monkeypatch.setenv("GOMODCACHE", str(tmp_path / "modcache"))
    monkeypatch.delenv("GOWORK", raising=False)

    root = tmp_path / "super"
    root.mkdir()
    stale = _member(root, "stale", "v1.0.0")
    _member(root, "fresh", "v1.1.0")
    (root / "go.work").write_text("go 1.22\n\nuse (\n\t./stale\n\t./fresh\n)\n", encoding="utf-8")
    return Workspace(path=stale, ecosystem="go", manager="go", git_repo=stale, label="stale")


def test_check_reports_update_masked_by_enclosing_go_work(workspace_member: Workspace) -> None:
    assert Change(_DEP, "v1.0.0", "v1.1.0") in check_go(workspace_member)


def test_apply_bumps_own_gomod_under_enclosing_go_work(workspace_member: Workspace) -> None:
    apply_go(workspace_member)
    assert f"{_DEP} v1.1.0" in (workspace_member.path / "go.mod").read_text(encoding="utf-8")
