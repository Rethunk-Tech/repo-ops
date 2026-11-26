"""Sandbox coverage for repo command execution."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from repo_ops_core.process import _runtime_dir_mount, _tool_cache_dirs, run


def test_run_home_writes_are_throwaway(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "seen").write_text("x")
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = run(
        ["sh", "-c", 'echo ok > "$HOME/outside" && mkdir "$HOME/d" && cat "$HOME/seen"'], cwd=repo
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "x"
    assert not (home / "outside").exists()
    assert not (home / "d").exists()


def test_run_exposes_tun_for_nested_pasta(tmp_path: Path) -> None:
    if not Path("/dev/net/tun").exists():
        pytest.skip("host has no /dev/net/tun")
    proc = run(["test", "-c", "/dev/net/tun"], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr


def test_run_in_workspace_member_writes_checkout_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    member = repo / "packages" / "api"
    member.mkdir(parents=True)
    proc = run(["sh", "-c", "echo lock > ../../bun.lock"], cwd=member)
    assert proc.returncode == 0, proc.stderr
    assert (repo / "bun.lock").read_text() == "lock\n"


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs ssh")
def test_run_lets_ssh_read_its_config_and_a_tests_own_options(tmp_path: Path, monkeypatch) -> None:
    # A GIT_SSH_COMMAND would override a test's own core.sshCommand, so none is set.
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    proc = run(
        ["sh", "-c", 'ssh -G localhost >/dev/null && printf %s "$GIT_SSH_COMMAND"'], cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


def test_run_allows_nested_bwrap(tmp_path: Path) -> None:
    proc = run(
        ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-user", "true"], cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr


def test_run_nested_tmp_overlay_on_home(tmp_path: Path, monkeypatch) -> None:
    """apply already overlays $HOME; inner run() must not nest --tmp-overlay."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    inner = (
        "from pathlib import Path\n"
        "from repo_ops_core.process import run\n"
        "r = run(['true'], Path('.'))\n"
        "print(r.stderr, end='')\n"
        "raise SystemExit(r.returncode)"
    )
    proc = run([sys.executable, "-c", inner], repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_run_runtime_dir_is_writable_and_private(tmp_path: Path, monkeypatch, request) -> None:
    # Outside /tmp: the sandbox binds the checkout root writable over anything beneath it.
    runtime = Path(tempfile.mkdtemp(dir="/var/tmp"))
    request.addfinalizer(lambda: shutil.rmtree(runtime, ignore_errors=True))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    proc = run(
        ["sh", "-c", 'mkdir "$XDG_RUNTIME_DIR/bus-x" && ls "$XDG_RUNTIME_DIR"'], cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    assert not (runtime / "bus-x").exists()


def test_tool_cache_dirs_cover_gate_isolate_caches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))  # no gate: the env-var fallback is what runs
    for var in ("GOLANGCI_LINT_CACHE", "PNPM_CONFIG_STORE_DIR", "RUSTUP_HOME", "DOTNET_CLI_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    dirs = _tool_cache_dirs()
    for want in (
        tmp_path / "GOLANGCI_LINT_CACHE",
        tmp_path / "PNPM_CONFIG_STORE_DIR",
        tmp_path / "RUSTUP_HOME",
        tmp_path / "DOTNET_CLI_HOME" / ".dotnet",
    ):
        assert str(want.resolve()) in dirs


def test_tool_cache_dirs_cover_sumdb_playwright_ccache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("CCACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    gopath = tmp_path / "gopath"
    monkeypatch.setenv("GOPATH", str(gopath))
    dirs = _tool_cache_dirs()
    for want in (
        gopath / "pkg" / "sumdb",
        tmp_path / ".cache" / "ms-playwright",
        tmp_path / ".cache" / "ccache",
    ):
        assert str(want.resolve()) in dirs


def test_runtime_dir_sweeps_dead_pid_dirs(monkeypatch, request) -> None:
    runtime = Path(tempfile.mkdtemp(dir="/var/tmp"))
    request.addfinalizer(lambda: shutil.rmtree(runtime, ignore_errors=True))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    stale, live = runtime / "repo-ops-4194999", runtime / f"repo-ops-{os.getppid()}"
    stale.mkdir()
    live.mkdir()
    mount = _runtime_dir_mount()
    assert not stale.exists()
    assert live.exists()
    shutil.rmtree(mount[1])


def test_runtime_dir_keeps_the_user_systemd_socket(monkeypatch, request) -> None:
    runtime = Path(tempfile.mkdtemp(dir="/var/tmp"))
    request.addfinalizer(lambda: shutil.rmtree(runtime, ignore_errors=True))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    (runtime / "systemd").mkdir()
    sock = socket.socket(socket.AF_UNIX)
    request.addfinalizer(sock.close)
    sock.bind(str(runtime / "systemd" / "private"))
    mount = _runtime_dir_mount()
    bus = str(runtime / "systemd" / "private")
    assert mount[3:] == ["--bind", bus, bus]
    shutil.rmtree(mount[1])
