"""Tests for Yarn isolated plan snapshots."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from repo_ops_js_yarn.yarn import (
    _command_failure,
    _copy_yarn_workspace_to_temp,
    _file_protocol_targets,
    _isolated_yarn_ignore,
    _layout_root,
    _resolved_versions,
    _workspace_declares_resolutions,
    package_json_paths,
)
from repo_ops_models import Workspace
from repo_ops_upgrade.constants import YARN_ISOLATED_COPY_SKIP


def test_yarn_not_in_isolated_copy_skip() -> None:
    assert ".yarn" not in YARN_ISOLATED_COPY_SKIP


def test_isolated_copy_skip_omits_retired_venv_name() -> None:
    assert ".dep_refresh_venv" not in YARN_ISOLATED_COPY_SKIP


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
            {"dependencies": {"@rechat/voice-interface": "file:../react-voice-interface#abc"}}
        ),
        encoding="utf-8",
    )
    targets = _file_protocol_targets(ws)
    assert targets == [sibling.resolve()]


def test_file_protocol_targets_reject_escape_above_workspace_parent(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "mono" / "app"
    ws.mkdir(parents=True)
    secret = tmp_path / "secret-dir"
    secret.mkdir()
    (secret / "package.json").write_text("{}", encoding="utf-8")
    (ws / "package.json").write_text(
        json.dumps({"dependencies": {"evil": "file:../../secret-dir"}}),
        encoding="utf-8",
    )
    assert _file_protocol_targets(ws) == []


def test_file_protocol_targets_keeps_nested_workspace_sibling(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    ws = repo / "packages" / "apps" / "web"
    ws.mkdir(parents=True)
    sibling = repo / "packages" / "shared"
    sibling.mkdir()
    (sibling / "package.json").write_text("{}", encoding="utf-8")
    (ws / "package.json").write_text(
        json.dumps({"dependencies": {"shared": "file:../../shared"}}),
        encoding="utf-8",
    )

    assert _file_protocol_targets(ws) == [sibling.resolve()]


def test_layout_root_common_parent(tmp_path: Path) -> None:
    mono = tmp_path / "mono"
    source = mono / "rechat"
    sibling = mono / "peer"
    source.mkdir(parents=True)
    sibling.mkdir()
    assert _layout_root(source, [sibling]) == mono


def test_layout_root_parent_when_trees_already_share_a_root_or_cannot_share_one(
    tmp_path: Path,
) -> None:
    child = tmp_path / "app"
    child.mkdir()
    assert _layout_root(tmp_path, []) == tmp_path.parent
    assert _layout_root(tmp_path, [child]) == tmp_path.parent
    assert _layout_root(Path("/workspace"), [Path("relative")]) == Path("/")


def test_package_json_paths_includes_workspace_packages(tmp_path: Path) -> None:
    root = tmp_path / "app"
    pkg = root / "packages" / "ui"
    pkg.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]}),
        encoding="utf-8",
    )
    (pkg / "package.json").write_text("{}", encoding="utf-8")
    paths = package_json_paths(root)
    assert root / "package.json" in paths
    assert pkg / "package.json" in paths


def test_workspace_declares_resolutions_false_without_pins(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "^1.0.0"}}),
        encoding="utf-8",
    )
    ws = Workspace(
        path=tmp_path,
        git_repo=tmp_path,
        ecosystem="node",
        manager="yarn",
        label="tmp",
    )
    assert _workspace_declares_resolutions(ws) is False


def test_workspace_declares_resolutions_true_with_pins(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"left-pad": "^1.0.0"},
                "resolutions": {"left-pad": "1.0.1"},
            }
        ),
        encoding="utf-8",
    )
    ws = Workspace(
        path=tmp_path,
        git_repo=tmp_path,
        ecosystem="node",
        manager="yarn",
        label="tmp",
    )
    assert _workspace_declares_resolutions(ws) is True


def test_file_protocol_targets_skip_unreadable_and_non_path_specs(tmp_path: Path) -> None:
    ws = tmp_path / "app"
    sibling = tmp_path / "shared"
    ws.mkdir()
    sibling.mkdir()
    (sibling / "package.json").write_text("{}", encoding="utf-8")
    (ws / "package.json").write_text(
        json.dumps(
            {
                "workspaces": ["packages/*"],
                "dependencies": "not-a-map",
                "devDependencies": {
                    "count": 1,
                    "plain": "^1.0.0",
                    "shared": "file:../shared",
                },
            }
        ),
        encoding="utf-8",
    )
    bad = ws / "packages" / "bad"
    bad.mkdir(parents=True)
    (bad / "package.json").write_text("{", encoding="utf-8")
    assert _file_protocol_targets(ws) == [sibling.resolve()]


def test_isolated_copy_keeps_nested_workspace_and_drops_skip_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    ws_path = repo / "packages" / "app"
    ws_path.mkdir(parents=True)
    (ws_path / "package.json").write_text("{}", encoding="utf-8")
    skipped = ws_path / "node_modules" / "left-pad"
    skipped.mkdir(parents=True)
    (skipped / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    dest_root = tmp_path / "copy"
    dest_root.mkdir()
    ws = Workspace(
        path=ws_path,
        git_repo=repo,
        ecosystem="node",
        manager="yarn",
        label="app",
    )
    copied = _copy_yarn_workspace_to_temp(ws, dest_root)
    assert copied == dest_root / "repo" / "packages" / "app"
    assert (copied / "package.json").is_file()
    assert not (copied / "node_modules").exists()
    assert _copy_yarn_workspace_to_temp(ws, dest_root) == copied


def test_isolated_copy_lays_out_file_protocol_sibling_outside_declared_git_repo(
    tmp_path: Path,
) -> None:
    declared_git = tmp_path / "elsewhere"
    declared_git.mkdir()
    mono = tmp_path / "mono"
    (mono / ".git").mkdir(parents=True)
    ws_path = mono / "app"
    sibling = mono / "shared"
    ws_path.mkdir()
    sibling.mkdir()
    (sibling / "package.json").write_text("{}", encoding="utf-8")
    (ws_path / "package.json").write_text(
        json.dumps({"dependencies": {"shared": "file:../shared"}}),
        encoding="utf-8",
    )
    dest_root = tmp_path / "copy"
    dest_root.mkdir()
    copied = _copy_yarn_workspace_to_temp(
        Workspace(
            path=ws_path,
            git_repo=declared_git,
            ecosystem="node",
            manager="yarn",
            label="app",
        ),
        dest_root,
    )
    assert copied == dest_root / "app"
    assert (copied / "package.json").is_file()
    assert (dest_root / "shared" / "package.json").is_file()


def test_yarn_failure_message_uses_stderr_then_appends_a_distinct_output_tail() -> None:
    empty = subprocess.CompletedProcess(["yarn"], 1, stdout="", stderr="")
    assert str(_command_failure("yarn up failed", empty)) == "yarn up failed"

    stderr_only = subprocess.CompletedProcess(["yarn"], 1, stdout="", stderr="boom\n")
    assert str(_command_failure("yarn up failed", stderr_only)) == "boom"

    mixed = subprocess.CompletedProcess(
        ["yarn"], 1, stdout="install log line\n", stderr="error: fail\n"
    )
    msg = str(_command_failure("yarn up failed", mixed))
    assert msg.startswith("error: fail")
    assert "install log line" in msg


def test_yarn_lock_duplicate_ranges_keep_the_highest_resolved_version(tmp_path: Path) -> None:
    lock = tmp_path / "yarn.lock"
    lock.write_text(
        "__metadata:\n"
        "  version: 8\n"
        "\n"
        '"left-pad@npm:^1.0.0":\n'
        "  version: 1.0.1\n"
        '  resolution: "left-pad@npm:1.0.1"\n'
        "\n"
        '"left-pad@npm:^1.3.0":\n'
        "  version: 1.3.0\n"
        '  resolution: "left-pad@npm:1.3.0"\n',
        encoding="utf-8",
    )
    assert _resolved_versions(lock) == {"left-pad": "1.3.0"}
