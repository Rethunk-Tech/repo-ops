"""Coverage for inventory's git-aware project discovery.

The prior implementation walked a fixed ``root/org/project`` directory pair,
which made three shapes invisible: checkouts nested deeper than two levels,
dot-named repos, and anything that was a repo but not at that exact depth. It
also counted plain directories as projects. These tests pin each shape.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from repo_ops_inventory.counting import scan
from repo_ops_inventory.scan import main as inventory_main


def _names(rows: list[dict]) -> set[str]:
    return {r["name"] for r in rows}


def test_repo_nested_deeper_than_two_levels_is_found(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path / "host" / "org" / "super", commit=True)
    init_git_repo(tmp_path / "host" / "org" / "super" / "packages" / "inner", commit=True)

    names = _names(scan(tmp_path))

    assert "host/org/super" in names
    assert "host/org/super/packages/inner" in names


def test_dot_named_repo_is_found(tmp_path: Path, init_git_repo) -> None:
    """`.github` repos are real checkouts, not hidden directories to skip."""
    init_git_repo(tmp_path / "org" / ".github", commit=True)

    assert "org/.github" in _names(scan(tmp_path))


def test_plain_directories_are_not_projects(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path / "org" / "real", commit=True)
    (tmp_path / "org" / "just-a-folder").mkdir(parents=True)
    (tmp_path / "org" / "just-a-folder" / "notes.txt").write_text("x\n", encoding="utf-8")

    assert _names(scan(tmp_path)) == {"org/real"}


def test_nested_repo_manifests_are_not_credited_to_the_parent(
    tmp_path: Path, init_git_repo
) -> None:
    """A superproject and its nested checkout must not both count the same deps."""
    parent = tmp_path / "org" / "parent"
    inner = parent / "packages" / "inner"
    init_git_repo(parent, commit=True)
    init_git_repo(inner, commit=True)
    (parent / "pyproject.toml").write_text('[project]\ndependencies = ["a"]\n', encoding="utf-8")
    (inner / "pyproject.toml").write_text(
        '[project]\ndependencies = ["b", "c"]\n', encoding="utf-8"
    )

    rows = {r["name"]: r for r in scan(tmp_path)}

    assert rows["org/parent"]["manifests"] == 1
    assert rows["org/parent"]["direct"] == 1
    assert rows["org/parent/packages/inner"]["manifests"] == 1
    assert rows["org/parent/packages/inner"]["direct"] == 2


def test_submodule_gitlink_checkout_gets_its_own_row(tmp_path: Path, init_git_repo) -> None:
    """A submodule's `.git` is a file, not a directory; it must still be a row."""
    parent = tmp_path / "org" / "parent"
    init_git_repo(parent, commit=True)
    sub_src = tmp_path / "subsrc"
    init_git_repo(sub_src, commit=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_src), "sub"],
        cwd=parent,
        check=True,
        capture_output=True,
    )

    names = _names(scan(tmp_path))

    assert "org/parent" in names
    assert "org/parent/sub" in names
    assert (parent / "sub" / ".git").is_file()


def test_inventory_json_reports_every_repo(tmp_path: Path, capsys, init_git_repo) -> None:
    init_git_repo(tmp_path / "org" / "a", commit=True)
    init_git_repo(tmp_path / "org" / "b" / "nested", commit=True)
    init_git_repo(tmp_path / "org" / "b", commit=True)

    rc = inventory_main([str(tmp_path), "--json"])

    assert rc == 0
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert {r["name"] for r in rows} == {"org/a", "org/b", "org/b/nested"}
