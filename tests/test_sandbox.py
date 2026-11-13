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
