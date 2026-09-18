"""Focused unit tests for uv workspace helpers."""

from __future__ import annotations

from pathlib import Path

from repo_ops_core.filesystem import _under, is_workspace_member
from repo_ops_py_uv.uv import (
    uv_workspace_members,
)


def test_uv_workspace_members_reads_globs(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/*", "apps/web"]\n',
        encoding="utf-8",
    )
    assert uv_workspace_members(tmp_path) == {"packages/*", "apps/web"}


def test_uv_workspace_members_empty_when_not_workspace(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "solo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    assert uv_workspace_members(tmp_path) == set()


def test_under_prefix_boundary() -> None:
    """packages/foo must not match sibling packages/foobar."""
    assert _under("packages/foo", "packages/foo") is True
    assert _under("packages/foo/bar", "packages/foo") is True
    assert _under("packages/foobar", "packages/foo") is False


def test_path_is_uv_workspace_child_glob(tmp_path: Path) -> None:
    root = tmp_path
    child = tmp_path / "packages" / "demo"
    child.mkdir(parents=True)
    members = {"packages/*"}
    assert is_workspace_member(child, root, members) is True
    sibling = tmp_path / "other" / "demo"
    sibling.mkdir(parents=True)
    assert is_workspace_member(sibling, root, members) is False
