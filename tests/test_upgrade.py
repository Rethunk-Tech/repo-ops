"""Lightweight tests for upgrade ecosystem helpers and safety regressions."""

from __future__ import annotations

import subprocess

import pytest

from repo_ops._register import register_all
from repo_ops_py.requirements import parse_requirements_lines

# normalize_version / semver_tuple are covered exhaustively in
# tests/test_version_utils.py; no need to duplicate that coverage here.


def test_parse_requirements_lines_basic():
    text = "foo==1.0\nbar==2.0\n# comment\n-baz==3"
    assert parse_requirements_lines(text) == {"foo": "1.0", "bar": "2.0"}


def test_parse_requirements_lines_extras_and_markers():
    """Extras must not drop the package; inline markers must not bleed into version."""
    text = 'requests[security]==2.31.0\nflask==3.0.0;sys_platform=="win32"\nplain==1.0.0\n'
    result = parse_requirements_lines(text)
    # extras: key is bare name, version is clean
    assert result["requests"] == "2.31.0"
    # marker: version stops at semicolon
    assert result["flask"] == "3.0.0"
    # plain name==version unchanged
    assert result["plain"] == "1.0.0"


def test_ecosystem_checker_and_applier_registry_keys():
    from repo_ops_upgrade import registry

    register_all()
    expected = {
        "bun",
        "npm",
        "pnpm",
        "yarn",
        "go",
        "rust",
        "uv",
        "poetry",
        "pip-tools",
        "hatch",
        "pdm",
        "pipenv",
        "deno",
        "container",
        "precommit",
    }
    assert expected == set(registry.CHECKERS.keys())
    assert set(registry.APPLIERS.keys()) == expected


def test_actions_discover_workflow_files_empty_without_github_dir(tmp_path):
    from repo_ops_upgrade.actions import discover_workflow_files

    assert discover_workflow_files(tmp_path) == []


@pytest.mark.parametrize(
    "manager,expected",
    [
        ("yarn", ["yarn"]),
        ("bun", ["bunx"]),
        ("npm", ["npx", "--yes"]),
        ("pnpm", ["pnpm", "exec"]),
    ],
)
def test_remediations_pm_exec_prefix(manager, expected):
    from repo_ops_js.workspaces import pm_exec_prefix

    assert pm_exec_prefix(manager) == expected


def test_resolved_version_from_node_modules_scoped(tmp_path):
    from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules

    pkg_dir = tmp_path / "node_modules" / "@biomejs" / "biome"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        '{"name":"@biomejs/biome","version":"2.0.0"}', encoding="utf-8"
    )
    assert resolved_version_from_node_modules(tmp_path, "@biomejs/biome") == "2.0.0"


def test_is_git_dirty_clean_repo(tmp_path):
    from repo_ops_core.git import is_git_dirty

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    assert is_git_dirty(tmp_path) is False


def test_is_git_dirty_ignores_dirty_submodule_content(tmp_path):
    from repo_ops_core.git import is_git_dirty

    def git(cwd, *args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "protocol.file.allow=always",
                *args,
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    child, parent = tmp_path / "child", tmp_path / "parent"
    for d in (child, parent):
        d.mkdir()
        git(d, "init", "-q")
    (child / "f").write_text("1")
    git(child, "add", "f")
    git(child, "commit", "-qm", "c")
    git(parent, "submodule", "add", "-q", str(child), "child")
    git(parent, "commit", "-qm", "p")
    assert is_git_dirty(parent) is False

    (parent / "child" / "f").write_text("2")
    assert is_git_dirty(parent) is False
    assert is_git_dirty(parent / "child") is True

    git(parent / "child", "commit", "-qam", "bump")
    assert is_git_dirty(parent) is True
