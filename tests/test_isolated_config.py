"""Repo commands must not rewrite the operator's user-global tool config."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import repo_ops_core.process as process
from repo_ops_core.process import isolate_user_config, run


@pytest.fixture
def operator_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for var in ("GIT_CONFIG_GLOBAL", "GOENV", "NPM_CONFIG_USERCONFIG", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    yield home
    process._ISOLATED_ENV.clear()


def test_git_config_global_write_leaves_operator_config(
    operator_home: Path, tmp_path: Path
) -> None:
    gitconfig = operator_home / ".gitconfig"
    gitconfig.write_text("[user]\n\tname = Operator\n")
    isolate_user_config(tmp_path / "scratch")

    proc = run(["git", "config", "--global", "user.name", "x"], tmp_path)
    assert proc.returncode == 0
    # repo-ops' own git keeps the real config, so the write above hit it.
    assert "x" in gitconfig.read_text()
    gitconfig.write_text("[user]\n\tname = Operator\n")

    proc = run(
        ["sh", "-c", "git config --global user.name y && git config --global user.name"], tmp_path
    )
    assert proc.stdout.strip() == "y"
    assert gitconfig.read_text() == "[user]\n\tname = Operator\n"
    assert (
        subprocess.run(
            ["git", "config", "--global", "user.name"], capture_output=True, text=True, check=False
        ).stdout.strip()
        == "Operator"
    )


@pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")
def test_go_env_write_leaves_operator_goenv(operator_home: Path, tmp_path: Path) -> None:
    goenv = operator_home / ".config" / "go" / "env"
    goenv.parent.mkdir(parents=True)
    goenv.write_text("GOPRIVATE=example.com/private\n")
    isolate_user_config(tmp_path / "scratch")

    proc = run(
        ["sh", "-c", "go env -w GOPROXY=https://example.invalid,direct && go env GOPRIVATE"],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "example.com/private"
    assert goenv.read_text() == "GOPRIVATE=example.com/private\n"
