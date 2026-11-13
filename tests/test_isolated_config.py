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
    for var in (
        "GIT_CONFIG_GLOBAL",
        "GOENV",
        "NPM_CONFIG_USERCONFIG",
        "PIP_CONFIG_FILE",
        "UV_CONFIG_FILE",
        "XDG_CONFIG_HOME",
    ):
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


def test_seeded_config_copies_are_private(operator_home: Path, tmp_path: Path) -> None:
    npmrc = operator_home / ".npmrc"
    npmrc.write_text("//registry.npmjs.org/:_authToken=secret\n")
    npmrc.chmod(0o600)
    scratch = tmp_path / "scratch"
    isolate_user_config(scratch)

    assert (scratch / "npmrc").stat().st_mode & 0o077 == 0
    assert scratch.stat().st_mode & 0o077 == 0


def test_caller_env_cannot_override_isolated_userconfig(
    operator_home: Path, tmp_path: Path
) -> None:
    npmrc = operator_home / ".npmrc"
    npmrc.write_text("original=1\n")
    isolate_user_config(tmp_path / "scratch")
    isolated = str((tmp_path / "scratch" / "npmrc").resolve())

    proc = run(
        ["sh", "-c", 'printf %s "$NPM_CONFIG_USERCONFIG"'],
        tmp_path,
        env={"NPM_CONFIG_USERCONFIG": str(npmrc)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == isolated


@pytest.mark.skipif(shutil.which("pip") is None, reason="pip not installed")
def test_pip_config_write_leaves_operator_pip_conf(operator_home: Path, tmp_path: Path) -> None:
    pipconf = operator_home / ".config" / "pip" / "pip.conf"
    pipconf.parent.mkdir(parents=True)
    original = "[global]\nindex-url = https://example.com/simple\n"
    pipconf.write_text(original)
    isolate_user_config(tmp_path / "scratch")

    run(["pip", "config", "set", "global.timeout", "99"], tmp_path)
    debug = run(["pip", "config", "debug"], tmp_path)
    assert str(tmp_path / "scratch" / "pip.conf") in debug.stdout
    assert pipconf.read_text() == original


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_uv_config_write_leaves_operator_uv_toml(operator_home: Path, tmp_path: Path) -> None:
    uvtoml = operator_home / ".config" / "uv" / "uv.toml"
    uvtoml.parent.mkdir(parents=True)
    original = "native-tls = false\n"
    uvtoml.write_text(original)
    isolate_user_config(tmp_path / "scratch")

    proc = run(["sh", "-c", "printf 'native-tls = true\\n' > \"$UV_CONFIG_FILE\""], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert uvtoml.read_text() == original
