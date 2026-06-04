"""Lightweight tests for upgrade ecosystem helpers and safety regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_core.version_utils import normalize_version, semver_tuple
from repo_ops_py.requirements import parse_requirements_lines


def test_normalize_version_strips_prefixes():
    assert normalize_version("^1.2.3") == "1.2.3"
    assert normalize_version("~>2.0") == "2.0"
    assert normalize_version(">=1.0.0-beta") == "1.0.0"


def test_semver_tuple_parses():
    assert semver_tuple("1.2.3") == (1, 2, 3)
    assert semver_tuple("^4.5") == (4, 5)


def test_parse_requirements_lines_basic():
    text = "foo==1.0\nbar==2.0\n# comment\n-baz==3"
    assert parse_requirements_lines(text) == {"foo": "1.0", "bar": "2.0"}


def test_parse_requirements_lines_extras_and_markers():
    """#87 — extras must not drop the package; inline markers must not bleed into version."""
    text = (
        'requests[security]==2.31.0\nflask==3.0.0;sys_platform=="win32"\nplain==1.0.0\n'
    )
    result = parse_requirements_lines(text)
    # extras: key is bare name, version is clean
    assert result["requests"] == "2.31.0"
    # marker: version stops at semicolon
    assert result["flask"] == "3.0.0"
    # plain name==version unchanged
    assert result["plain"] == "1.0.0"


def test_checkers_use_monkeypatched_run(monkeypatch):
    from repo_ops_js_bun.bun import check_bun
    from repo_ops_models import Workspace

    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "\x1b[1mbun outdated v1.3.14 (0d9b296a)\x1b[0m\n"
                "│ Package │ Current │ Update │ Latest │\n"
                "│ foo │ 1.0 │ 1.1 │ 1.1 │\n"
            ),
            stderr="",
        )

    ws = Workspace(
        path=Path("/tmp"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp"),
        label="",
    )
    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes, behind_latest = check_bun(ws)
    assert len(changes) == 1
    assert changes[0].name == "foo"
    assert behind_latest == []
    assert any("bun" in c[0] for c in calls)


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
    }
    assert expected == set(registry.CHECKERS.keys())
    assert set(registry.APPLIERS.keys()) == expected


def test_actions_discover_workflow_files_empty_without_github_dir(tmp_path):
    from repo_ops_upgrade.actions import discover_workflow_files

    assert discover_workflow_files(tmp_path) == []


def test_remediations_pm_exec_prefix_yarn():
    from repo_ops_js.workspaces import pm_exec_prefix

    assert pm_exec_prefix("yarn") == ["yarn"]


def test_resolved_version_from_node_modules_scoped(tmp_path):
    from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules

    pkg_dir = tmp_path / "node_modules" / "@biomejs" / "biome"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        '{"name":"@biomejs/biome","version":"2.0.0"}', encoding="utf-8"
    )
    assert resolved_version_from_node_modules(tmp_path, "@biomejs/biome") == "2.0.0"


def test_check_rust_parses_stderr_updates(monkeypatch):
    """cargo update --dry-run writes to stderr; check_rust must not miss it."""
    from repo_ops_models import Workspace
    from repo_ops_rust.rust import check_rust

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="",
            stderr="    Updating serde v1.0.100 -> v1.0.200\n",
        )

    ws = Workspace(
        path=Path("/tmp"),
        ecosystem="rust",
        manager="rust",
        git_repo=Path("/tmp"),
        label="",
    )
    with patch("repo_ops_rust.rust.run", fake_run):
        changes = check_rust(ws)
    assert len(changes) == 1
    assert changes[0].name == "serde"
    assert changes[0].from_ver == "1.0.100"
    assert changes[0].to == "1.0.200"


def test_is_git_dirty_clean_repo(tmp_path):
    from repo_ops_core.git import is_git_dirty

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    assert is_git_dirty(tmp_path) is False


def test_apply_poetry_raises_clear_error_when_poetry_absent(tmp_path):
    """apply_poetry must raise RuntimeError with 'poetry not on PATH', not bare FileNotFoundError."""
    import pytest
    from repo_ops_models import Workspace
    from repo_ops_py_poetry.poetry import apply_poetry

    ws = Workspace(
        path=tmp_path,
        ecosystem="python",
        manager="poetry",
        git_repo=tmp_path,
        label="",
    )
    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="poetry not on PATH"),
    ):
        apply_poetry(ws)
