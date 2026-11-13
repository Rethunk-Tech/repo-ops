"""Sandbox coverage for repo command execution."""

from __future__ import annotations

from pathlib import Path

from repo_ops_core.process import run


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
