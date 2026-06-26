"""Regression test: Pipfile.lock-only repo (no Pipfile) must be discovered as pipenv.

Bug: the outer trigger set in discover_workspaces_in_root only checked for
'Pipfile' (not 'Pipfile.lock'), so a repo with only Pipfile.lock was silently
dropped and never reached consider_py.  Fixed by adding 'Pipfile.lock' to the
trigger set and to the consider_py guard condition.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repo_ops_upgrade.discovery import discover_all


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_pipfile_lock_only_discovered_as_pipenv(tmp_path: Path) -> None:
    """Pipfile.lock without Pipfile must be discovered as manager=pipenv (regression #20)."""
    repo = tmp_path / "org" / "pipfile-lock-only"
    _git_init(repo)
    # Only the lockfile exists — no Pipfile, no pyproject.toml
    (repo / "Pipfile.lock").write_text(
        '{"_meta":{},"default":{"requests":{"version":"==2.31.0"}},"develop":{}}'
    )

    found = discover_all([str(tmp_path)], flat=False)
    pipenv_found = [ws for ws in found if ws.manager == "pipenv"]

    assert len(pipenv_found) == 1, (
        f"Pipfile.lock-only repo must be discovered as pipenv; found={pipenv_found}"
    )
    assert pipenv_found[0].ecosystem == "py"
    assert pipenv_found[0].path == repo.resolve()


def test_pipfile_lock_only_not_double_counted_with_pipfile(tmp_path: Path) -> None:
    """When both Pipfile and Pipfile.lock exist, exactly one pipenv workspace is found."""
    repo = tmp_path / "org" / "full-pipenv"
    _git_init(repo)
    (repo / "Pipfile").write_text('[packages]\nrequests = "*"\n')
    (repo / "Pipfile.lock").write_text(
        '{"_meta":{},"default":{"requests":{"version":"==2.31.0"}},"develop":{}}'
    )

    found = discover_all([str(tmp_path)], flat=False)
    pipenv_found = [ws for ws in found if ws.manager == "pipenv"]

    assert len(pipenv_found) == 1, (
        f"Pipfile + Pipfile.lock must yield exactly one pipenv workspace; found={pipenv_found}"
    )
