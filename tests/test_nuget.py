"""NuGet pin discovery, hold filtering and in-place rewrites."""

import gzip
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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


def test_sdk_pins_and_lockfiles_are_skipped_when_unusable(tmp_path: Path) -> None:
    assert nuget._sdk_pins(tmp_path / "missing.json") == {}
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "not-sdks.json").write_text('{"msbuild-sdks": []}')
    assert nuget._sdk_pins(tmp_path / "broken.json") == {}
    assert nuget._sdk_pins(tmp_path / "not-sdks.json") == {}
    (tmp_path / "packages.lock.json").write_text("{}")
    (tmp_path / "App.csproj").write_text("<Project />")
    assert "packages.lock.json" in {p.name for p in nuget.find_nuget_files(tmp_path)}
    assert nuget._pins(tmp_path) == []
    assert nuget.is_nuget_root({"App.csproj", "README.md"})
    assert not nuget.is_nuget_root({"packages.lock.json", "global.json"})


def test_feeds_read_nuget_config_up_the_repo_root(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    proj = tmp_path / "src" / "app"
    proj.mkdir(parents=True)
    (proj / "App.csproj").write_text("<Project />")
    (tmp_path / "NuGet.Config").write_text(
        '<add key="priv" value="https://priv.example/v3/index.json" />\n'
    )
    ws = Workspace(path=proj, ecosystem="dotnet", manager="nuget", git_repo=tmp_path, label="")
    assert nuget._feeds(ws) == (nuget.NUGET_ORG, "https://priv.example/v3/index.json")


class _Resp:
    def __init__(self, data: bytes, encoding: str | None = None) -> None:
        self._data = data
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_get_json_decodes_plain_and_gzip_bodies() -> None:
    payload = {"ok": True}

    def open_plain(url: str, timeout: float, headers: dict[str, str]) -> _Resp:
        assert url == "https://feed.test/index.json"
        return _Resp(json.dumps(payload).encode())

    with patch.object(nuget, "open_url", side_effect=open_plain):
        assert nuget._get_json("https://feed.test/index.json") == payload

    def open_gzip(url: str, timeout: float, headers: dict[str, str]) -> _Resp:
        return _Resp(gzip.compress(json.dumps(payload).encode()), encoding="gzip")

    with patch.object(nuget, "open_url", side_effect=open_gzip):
        assert nuget._get_json("https://feed.test/index.json") == payload


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://feed.test/x", code, "err", None, None)


def test_lookup_maps_http_and_io_failures() -> None:
    with patch.object(nuget, "_get_json", side_effect=_http_error(404)):
        assert nuget._lookup("https://feed.test", "Pkg", "https://feed.test/missing") is None
    with (
        patch.object(nuget, "_get_json", side_effect=_http_error(500)),
        pytest.raises(RuntimeError, match="HTTP 500"),
    ):
        nuget._lookup("https://feed.test", "Pkg", "https://feed.test/pkg")
    with (
        patch.object(nuget, "_get_json", side_effect=OSError("offline")),
        pytest.raises(RuntimeError, match="offline"),
    ):
        nuget._lookup("https://feed.test", "Pkg", "https://feed.test/pkg")


def test_feed_versions_falls_back_when_registration_is_unusable() -> None:
    documents = {
        "https://flat.test/index.json": {
            "resources": [
                "skip",
                {"@id": 1},
                {"@type": "PackageBaseAddress/3.0.0", "@id": "https://flat.test/v3-flat/"},
            ]
        },
        "https://flat.test/v3-flat/pkg/index.json": {"versions": ["1.2.3", 1]},
        "https://mix.test/index.json": {
            "resources": [
                {"@type": "RegistrationsBaseUrl", "@id": "https://mix.test/reg/"},
                {"@type": "PackageBaseAddress/3.0.0", "@id": "https://mix.test/flat/"},
            ]
        },
        "https://mix.test/reg/gone/index.json": _http_error(404),
        "https://mix.test/reg/badpages/index.json": {"items": "nope"},
        "https://mix.test/flat/badpages/index.json": {"versions": ["0.1.0"]},
        "https://mix.test/reg/nonpage/index.json": {"items": [1]},
        "https://mix.test/flat/nonpage/index.json": {"versions": ["0.2.0"]},
        "https://mix.test/reg/noleaves/index.json": {"items": [{}]},
        "https://mix.test/flat/noleaves/index.json": None,
        "https://empty.test/index.json": {"resources": []},
    }

    def get_json(url: str) -> object:
        value = documents[url]
        if isinstance(value, urllib.error.HTTPError):
            raise value
        return value

    nuget._resources.cache_clear()
    nuget._feed_versions.cache_clear()
    with patch.object(nuget, "_get_json", side_effect=get_json):
        assert nuget._feed_versions("https://flat.test/index.json", "Pkg") == ("1.2.3",)
        assert nuget._feed_versions("https://mix.test/index.json", "Gone") == ()
        assert nuget._feed_versions("https://mix.test/index.json", "BadPages") == ("0.1.0",)
        assert nuget._feed_versions("https://mix.test/index.json", "NonPage") == ("0.2.0",)
        assert nuget._feed_versions("https://mix.test/index.json", "NoLeaves") == ()
        with pytest.raises(RuntimeError, match="no registration or PackageBaseAddress"):
            nuget._feed_versions("https://empty.test/index.json", "Pkg")


def test_check_skips_current_and_allows_the_best_held_ceiling(tmp_path: Path) -> None:
    ws = _workspace(
        tmp_path,
        """[[hold]]
package = "Held.Lib"
max_version = "1"
""",
    )
    (tmp_path / "Extra.csproj").write_text(
        """<Project>
  <ItemGroup>
    <PackageReference Include="Held.Lib" Version="1.0.0" />
    <PackageReference Include="Done.Lib" Version="1.0.0" />
  </ItemGroup>
</Project>
"""
    )

    def versions(feed: str, package: str) -> tuple[str, ...]:
        extra = {"held.lib": ("1.0.0", "1.2.0", "2.0.0"), "done.lib": ("1.0.0",)}
        if package.lower() in extra:
            return extra[package.lower()] if feed == "https://feed.example/v3/index.json" else ()
        return _versions(feed, package)

    with patch.object(nuget, "_feed_versions", side_effect=versions):
        allowed, held = nuget.check_nuget(ws)
    assert Change("Held.Lib", "1.0.0", "1.2.0") in allowed
    assert Change("Held.Lib", "1.0.0", "2.0.0") in held
    assert all(c.name != "Done.Lib" for c in (*allowed, *held))


def test_rewrite_skips_a_mismatched_version_and_restore_prefers_solutions(
    tmp_path: Path,
) -> None:
    csproj = tmp_path / "App.csproj"
    csproj.write_text('<PackageReference Include="Foo" Version="1.0.0" />')
    assert nuget._rewrite(csproj, "Foo", "9.9.9", "2.0.0", tmp_path) is False
    assert nuget._restore_targets(tmp_path) == [csproj]
    sln = tmp_path / "App.sln"
    sln.write_text("")
    assert nuget._restore_targets(tmp_path) == [sln]


def test_restore_returns_dotnet_output(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "App.csproj").write_text("<Project />")
    ws = Workspace(path=tmp_path, ecosystem="dotnet", manager="nuget", git_repo=tmp_path, label="")
    fail = SimpleNamespace(returncode=1, stdout="", stderr="")
    with patch.object(nuget, "run", return_value=fail):
        assert nuget._restore(ws) == "dotnet restore App.csproj failed"
    ok = SimpleNamespace(returncode=0, stdout="", stderr="")
    with patch.object(nuget, "run", return_value=ok):
        assert nuget._restore(ws) is None
    noisy = SimpleNamespace(returncode=1, stdout="out", stderr="err")
    with patch.object(nuget, "run", return_value=noisy):
        assert nuget._restore(ws) == "outerr"


def test_apply_returns_planned_changes_when_writes_are_blocked(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    with (
        patch.object(nuget, "_feed_versions", side_effect=_versions),
        patch.object(nuget, "guard_tool_writes", return_value=False),
    ):
        remaining = nuget.apply_nuget(ws)
    assert remaining == [
        Change("BepInEx.Core", "5.4.21", "6.0.0"),
        Change("GameLibs", "81.0.5-ngd.0", "82.0.0"),
        Change("ThunderPipe.Sdk", "1.0.0", "1.0.4"),
    ]
    assert (tmp_path / "Directory.Build.targets").read_text() == _TARGETS


def test_snapshot_changes_report_pin_diffs() -> None:
    proj = Path("App.csproj")
    gj = Path("global.json")
    lock = Path("packages.lock.json")
    before = {
        proj: b'<Project><PackageReference Include="Foo" Version="1.0.0" /></Project>',
        gj: b'{"msbuild-sdks": {"Sdk": "1.0.0"}}',
        lock: b"{}",
    }
    after = {
        proj: (
            b'<Project><PackageReference Include="Foo" Version="2.0.0" />'
            b'<PackageReference Include="Bar" Version="1.0.0" /></Project>'
        ),
        gj: b"{",
        lock: b"{changed}",
    }
    changes = nuget.nuget_snapshot_changes(before, after)
    assert Change("Foo", "1.0.0", "2.0.0") in changes
    assert Change("Bar", "?", "1.0.0") in changes
    assert nuget.nuget_snapshot_changes({gj: b'{"msbuild-sdks": {"Sdk": "1"}}'}, {gj: b"[]"}) == []
