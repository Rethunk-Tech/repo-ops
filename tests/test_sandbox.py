"""Sandbox coverage for repo command execution."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from repo_ops_core.process import _tool_cache_dirs, run


def test_run_blocks_write_to_home_outside(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = run(["sh", "-c", 'echo pwned > "$HOME/outside"'], cwd=repo)
    assert proc.returncode != 0
    assert not (home / "outside").exists()


def test_run_in_workspace_member_writes_checkout_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    member = repo / "packages" / "api"
    member.mkdir(parents=True)
    proc = run(["sh", "-c", "echo lock > ../../bun.lock"], cwd=member)
    assert proc.returncode == 0, proc.stderr
    assert (repo / "bun.lock").read_text() == "lock\n"


def test_run_points_git_ssh_past_system_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    proc = run(["sh", "-c", 'printf %s "$GIT_SSH_COMMAND"'], cwd=tmp_path)
    assert proc.stdout == "ssh -F /dev/null"


def test_run_allows_nested_bwrap(tmp_path: Path) -> None:
    proc = run(
        ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-user", "true"], cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr


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


def test_tool_cache_dirs_cover_sumdb_playwright_ccache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("CCACHE_DIR", raising=False)
    gopath = tmp_path / "gopath"
    monkeypatch.setenv("GOPATH", str(gopath))
    dirs = _tool_cache_dirs()
    for want in (
        gopath / "pkg" / "sumdb",
        tmp_path / ".cache" / "ms-playwright",
        tmp_path / ".cache" / "ccache",
    ):
        assert str(want.resolve()) in dirs
