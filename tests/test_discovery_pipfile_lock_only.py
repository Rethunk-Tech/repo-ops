"""Regression test: Pipfile.lock-only repo (no Pipfile) must be discovered as pipenv.

Bug: the outer trigger set in discover_workspaces_in_root only checked for
'Pipfile' (not 'Pipfile.lock'), so a repo with only Pipfile.lock was silently
dropped and never reached consider_py.  Fixed by adding 'Pipfile.lock' to the
trigger set and to the consider_py guard condition.
"""

from __future__ import annotations

from pathlib import Path

from repo_ops_upgrade.discovery import discover_all


def test_pipfile_lock_only_discovered_as_pipenv(tmp_path: Path, init_git_repo) -> None:
    """Pipfile.lock without Pipfile must be discovered as manager=pipenv."""
    repo = tmp_path / "org" / "pipfile-lock-only"
    init_git_repo(repo)
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


def test_pipfile_lock_only_not_double_counted_with_pipfile(tmp_path: Path, init_git_repo) -> None:
    """When both Pipfile and Pipfile.lock exist, exactly one pipenv workspace is found."""
    repo = tmp_path / "org" / "full-pipenv"
    init_git_repo(repo)
    (repo / "Pipfile").write_text('[packages]\nrequests = "*"\n')
    (repo / "Pipfile.lock").write_text(
        '{"_meta":{},"default":{"requests":{"version":"==2.31.0"}},"develop":{}}'
    )

    found = discover_all([str(tmp_path)], flat=False)
    pipenv_found = [ws for ws in found if ws.manager == "pipenv"]

    assert len(pipenv_found) == 1, (
        f"Pipfile + Pipfile.lock must yield exactly one pipenv workspace; found={pipenv_found}"
    )
