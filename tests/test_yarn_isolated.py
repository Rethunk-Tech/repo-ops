"""Tests for Yarn isolated plan snapshots (issue #6)."""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_js_yarn.yarn import (
    _file_protocol_targets,
    _isolated_yarn_ignore,
    _layout_root,
    _package_json_paths,
)
from repo_ops_upgrade.constants import YARN_ISOLATED_COPY_SKIP


def test_yarn_not_in_isolated_copy_skip() -> None:
    assert ".yarn" not in YARN_ISOLATED_COPY_SKIP


def test_isolated_yarn_ignore_keeps_releases() -> None:
    ignored = _isolated_yarn_ignore("/proj/.yarn", ["releases", "cache", "unplugged"])
    assert "releases" not in ignored
    assert "cache" in ignored
    assert "unplugged" in ignored


def test_file_protocol_targets_resolve_sibling(tmp_path: Path) -> None:
    mono = tmp_path / "mono"
    ws = mono / "rechat"
    sibling = mono / "react-voice-interface"
    ws.mkdir(parents=True)
    sibling.mkdir()
    (sibling / "package.json").write_text("{}", encoding="utf-8")
    (ws / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@rechat/voice-interface": "file:../react-voice-interface#abc"
                }
            }
        ),
        encoding="utf-8",
    )
    targets = _file_protocol_targets(ws)
    assert targets == [sibling.resolve()]


def test_layout_root_common_parent(tmp_path: Path) -> None:
    mono = tmp_path / "mono"
    source = mono / "rechat"
    sibling = mono / "peer"
    source.mkdir(parents=True)
    sibling.mkdir()
    assert _layout_root(source, [sibling]) == mono


def test_package_json_paths_includes_workspace_packages(tmp_path: Path) -> None:
    root = tmp_path / "app"
    pkg = root / "packages" / "ui"
    pkg.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]}),
        encoding="utf-8",
    )
    (pkg / "package.json").write_text("{}", encoding="utf-8")
    paths = _package_json_paths(root)
    assert root / "package.json" in paths
    assert pkg / "package.json" in paths
