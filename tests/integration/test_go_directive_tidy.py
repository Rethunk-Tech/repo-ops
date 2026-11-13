"""Integration: raising go.mod's go directive leaves a module that still builds.

From go 1.17 on, go.mod must list every module the build needs, so moving a
1.16 module's directive past it without tidying fails ``go build`` with
"updates to go.mod needed". Modules are local replaces, so no network is touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from repo_ops_models import Workspace
from repo_ops_upgrade import toolchains

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("go") is None, reason="go not on PATH"),
]

_ENV = {**os.environ, "GOWORK": "off", "GOPROXY": "off", "GOTOOLCHAIN": "local"}


def _go(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["go", *args], cwd=cwd, capture_output=True, text=True, env=_ENV)


def _module(path: Path, name: str, body: str, gomod_tail: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "go.mod").write_text(f"module example.com/{name}\n\ngo 1.16\n{gomod_tail}")
    (path / f"{name}.go").write_text(body)


def test_raised_go_directive_is_tidied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = _go(tmp_path, "env", "GOVERSION").stdout.strip().removeprefix("go")
    _module(tmp_path / "b", "b", "package b\n\nconst V = 1\n")
    _module(
        tmp_path / "a",
        "a",
        'package a\n\nimport "example.com/b"\n\nconst V = b.V\n',
        "\nrequire example.com/b v0.0.0\n",
    )
    root = tmp_path / "main"
    _module(
        root,
        "main",
        'package main\n\nimport "example.com/a"\n\nfunc main() { _ = a.V }\n',
        "\nrequire example.com/a v0.0.0\n\n"
        "replace example.com/a => ../a\n\nreplace example.com/b => ../b\n",
    )
    assert _go(root, "build", "./...").returncode == 0
    monkeypatch.setattr(toolchains, "_latest_go", lambda _cur: local)

    changes, _ = toolchains.apply_toolchains(
        Workspace(path=root, ecosystem="go", manager="go", git_repo=root, label=".")
    )

    assert [c.name for c in changes] == ["go"]
    built = _go(root, "build", "-mod=readonly", "./...")
    assert built.returncode == 0, built.stderr
