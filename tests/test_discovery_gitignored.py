from __future__ import annotations

from pathlib import Path

from repo_ops_upgrade.discovery import discover_all


def test_gitignored_build_output_is_not_a_workspace(tmp_path: Path, init_git_repo) -> None:
    repo = tmp_path / "org" / "app"
    init_git_repo(repo)
    (repo / ".gitignore").write_text("dist-release/\n")
    (repo / "go.mod").write_text("module example.com/app\n\ngo 1.27\n")
    copy = repo / "dist-release" / "server"
    copy.mkdir(parents=True)
    (copy / "go.mod").write_text("module example.com/app\n\ngo 1.27\n")

    found = discover_all([str(tmp_path)])

    assert [ws.path for ws in found if ws.ecosystem == "go"] == [repo.resolve()]
