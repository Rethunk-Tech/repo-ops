"""NuGet pin discovery, hold filtering and in-place rewrites."""

from pathlib import Path
from unittest.mock import patch

from repo_ops_core.holds import clear_hold_caches
from repo_ops_models import Change, Workspace
from repo_ops_nuget import nuget

_TARGETS = """<Project>
  <PropertyGroup>
    <RestoreAdditionalProjectSources>
      https://feed.example/v3/index.json;
    </RestoreAdditionalProjectSources>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="BepInEx.Core" Version="5.4.21" />
    <PackageReference Include="GameLibs" Version="81.0.5-ngd.0" PrivateAssets="all" />
    <PackageReference Include="Ranged" Version="[1.0,2.0)" />
    <PackageReference Include="Prop" Version="$(PropVersion)" />
  </ItemGroup>
</Project>
"""
_GLOBAL_JSON = '{\n  "msbuild-sdks": {\n    "ThunderPipe.Sdk": "1.0.0"\n  }\n}\n'
_FEEDS = {
    "bepinex.core": ("5.4.20", "5.4.21", "6.0.0-be.791", "6.0.0"),
    "gamelibs": ("81.0.5-ngd.0", "81.0.6-ngd.0", "82.0.0"),
    "thunderpipe.sdk": ("1.0.0", "1.0.4"),
}
_HOLDS = """[[hold]]
package = "GameLibs"
max_version = "81.0.5-ngd.0"

[[hold]]
package = "BepInEx.Core"
max_version = "5"
"""


def _workspace(tmp_path: Path, holds: str = "") -> Workspace:
    (tmp_path / ".git").mkdir()
    (tmp_path / "Mod.slnx").write_text("<Solution />\n")
    (tmp_path / "Directory.Build.targets").write_text(_TARGETS)
    (tmp_path / "global.json").write_text(_GLOBAL_JSON)
    (tmp_path / "obj").mkdir()
    (tmp_path / "obj" / "Copy.csproj").write_text(_TARGETS)
    if holds:
        (tmp_path / ".repo-ops.toml").write_text(holds)
    clear_hold_caches()
    return Workspace(
        path=tmp_path, ecosystem="dotnet", manager="nuget", git_repo=tmp_path, label=""
    )


def _versions(feed: str, package: str) -> tuple[str, ...]:
    return _FEEDS.get(package.lower(), ()) if feed == "https://feed.example/v3/index.json" else ()


def test_check_reads_literal_pins_and_the_feeds_projects_add(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    with patch.object(nuget, "_feed_versions", side_effect=_versions):
        allowed, held = nuget.check_nuget(ws)
    assert held == []
    assert sorted(allowed, key=lambda c: c.name) == [
        Change("BepInEx.Core", "5.4.21", "6.0.0"),
        Change("GameLibs", "81.0.5-ngd.0", "82.0.0"),
        Change("ThunderPipe.Sdk", "1.0.0", "1.0.4"),
    ]


def test_holds_cap_a_prerelease_pin_exactly_and_a_major(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, _HOLDS)
    with patch.object(nuget, "_feed_versions", side_effect=_versions):
        allowed, held = nuget.check_nuget(ws)
    assert allowed == [Change("ThunderPipe.Sdk", "1.0.0", "1.0.4")]
    assert {c.name for c in held} == {"BepInEx.Core", "GameLibs"}


def test_apply_rewrites_pins_and_reverts_a_failed_restore(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    restores = iter([None, "NU1102: Unable to find package", None])
    with (
        patch.object(nuget, "_feed_versions", side_effect=_versions),
        patch.object(nuget, "_restore", side_effect=lambda _ws: next(restores)),
    ):
        remaining = nuget.apply_nuget(ws)
    targets = (tmp_path / "Directory.Build.targets").read_text()
    assert 'Include="BepInEx.Core" Version="6.0.0"' in targets
    assert 'Include="GameLibs" Version="81.0.5-ngd.0" PrivateAssets="all"' in targets
    assert '"ThunderPipe.Sdk": "1.0.4"' in (tmp_path / "global.json").read_text()
    assert remaining == [Change("GameLibs", "81.0.5-ngd.0", "82.0.0")]
    assert (tmp_path / "obj" / "Copy.csproj").read_text() == _TARGETS


def test_version_key_orders_four_parts_and_prereleases() -> None:
    ordered = ["1.0.0-alpha.2", "1.0.0-alpha.10", "1.0.0", "1.0.0.1", "1.0.1"]
    shuffled = [ordered[i] for i in (3, 0, 4, 2, 1)]
    assert sorted(shuffled, key=nuget.nuget_version_key) == ordered


def test_feed_versions_skip_unlisted_and_follow_registration_pages() -> None:
    documents = {
        "https://feed.test/index.json": {
            "resources": [
                {"@type": "RegistrationsBaseUrl", "@id": "https://feed.test/semver1/"},
                {"@type": "RegistrationsBaseUrl/3.6.0", "@id": "https://feed.test/semver2/"},
            ]
        },
        "https://feed.test/semver2/pkg/index.json": {
            "items": [
                {"items": [{"catalogEntry": {"version": "1.0.0"}}]},
                {"@id": "https://feed.test/semver2/pkg/page2.json"},
            ]
        },
        "https://feed.test/semver2/pkg/page2.json": {
            "items": [
                {"catalogEntry": {"version": "1.0.1-alpha.2", "listed": True}},
                {"catalogEntry": {"version": "1.0.4", "listed": False}},
            ]
        },
    }
    nuget._resources.cache_clear()
    nuget._feed_versions.cache_clear()
    with patch.object(nuget, "_get_json", side_effect=documents.__getitem__):
        assert nuget._feed_versions("https://feed.test/index.json", "Pkg") == (
            "1.0.0",
            "1.0.1-alpha.2",
        )
