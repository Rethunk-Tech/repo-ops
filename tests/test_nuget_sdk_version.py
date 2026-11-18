"""``global.json`` ``sdk.version`` follows its feature band, per Microsoft's release index."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.holds import clear_hold_caches
from repo_ops_models import Workspace
from repo_ops_nuget import nuget

INDEX = {
    "releases-index": [
        {"channel-version": "10.0", "releases.json": "https://dotnet.test/10.0/releases.json"},
        {"channel-version": "9.0", "releases.json": "https://dotnet.test/9.0/releases.json"},
    ]
}
RELEASES = {
    "https://dotnet.test/10.0/releases.json": {
        "releases": [
            {"sdk": {"version": "10.0.100"}},
            {"sdk": {"version": "10.0.104"}},
            {"sdk": {"version": "10.0.200"}},
            {"sdk": {"version": "10.0.203"}},
            {"sdk": {"version": "11.0.100"}},
        ]
    },
    "https://dotnet.test/9.0/releases.json": {"releases": [{"sdk": {"version": "9.0.300"}}]},
}


@pytest.fixture(autouse=True)
def _index(monkeypatch: pytest.MonkeyPatch) -> None:
    documents = {nuget._SDK_INDEX: INDEX, **RELEASES}
    monkeypatch.setattr(nuget, "_get_json", documents.__getitem__)
    nuget.sdk_versions.cache_clear()
    clear_hold_caches()


def _ws(path: Path) -> Workspace:
    return Workspace(path=path, ecosystem="dotnet", manager="nuget", git_repo=path, label="x")


def _project(path: Path) -> None:
    (path / "App.csproj").write_text(
        "<Project><ItemGroup>"
        '<PackageReference Include="ThunderPipe.Sdk" Version="1.0.4" /></ItemGroup></Project>'
    )


def test_sdk_version_moves_to_the_newest_patch_in_its_band(tmp_path: Path) -> None:
    global_json = tmp_path / "global.json"
    global_json.write_text(json.dumps({"sdk": {"version": "10.0.100"}}, indent=2) + "\n")
    _project(tmp_path)

    with (
        patch.object(nuget, "_feed_versions", return_value=("1.0.4",)),
        patch.object(nuget, "_restore", return_value=None),
    ):
        changes, held = nuget.check_nuget(_ws(tmp_path))
        nuget.apply_nuget(_ws(tmp_path))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [
        (nuget.SDK_NAME, "10.0.100", "10.0.104")
    ]
    assert held == []
    # The 10.0.200 band and the 11.0 major are both a different band.
    assert json.loads(global_json.read_text())["sdk"]["version"] == "10.0.104"


def test_roll_forward_disable_leaves_the_pin_at_its_own_policy(tmp_path: Path) -> None:
    global_json = tmp_path / "global.json"
    global_json.write_text(
        json.dumps({"sdk": {"version": "10.0.100", "rollForward": "disable"}}, indent=2) + "\n"
    )
    _project(tmp_path)

    with patch.object(nuget, "_feed_versions", return_value=("1.0.4",)):
        changes, _held = nuget.check_nuget(_ws(tmp_path))

    assert changes == []
    assert '"10.0.100"' in global_json.read_text()


def test_a_hold_named_dotnet_caps_the_sdk(tmp_path: Path) -> None:
    (tmp_path / ".repo-ops.toml").write_text(
        '[[hold]]\npackage = "dotnet"\nmax_version = "10.0.100"\n'
    )
    global_json = tmp_path / "global.json"
    global_json.write_text(json.dumps({"sdk": {"version": "10.0.100"}}, indent=2) + "\n")
    _project(tmp_path)

    with (
        patch.object(nuget, "_feed_versions", return_value=("1.0.4",)),
        patch.object(nuget, "_restore", return_value=None),
    ):
        changes, held = nuget.check_nuget(_ws(tmp_path))
        nuget.apply_nuget(_ws(tmp_path))

    assert changes == []
    assert [(h.name, h.to) for h in held] == [(nuget.SDK_NAME, "10.0.104")]
    assert '"10.0.100"' in global_json.read_text()
