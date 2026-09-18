"""Rust wiring for package version-ceiling ("hold") rules."""

import json
from unittest.mock import patch

from repo_ops_core.holds import HoldRule
from repo_ops_rust.rust import (
    _cargo_registry_versions,
    apply_rust,
    check_rust,
)

_GLOBAL_HOLDS_CACHE = "repo_ops_core.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_core.holds._repo_holds_cached"
_REGISTRY_VERSIONS = "repo_ops_rust.rust._cargo_registry_versions"
_REGISTRY_CONNECTION = "repo_ops_rust.rust.HTTPSConnection"
_CRATE = "serde"


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _CRATE, "max_version": "1.5"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _metadata(crate_versions: str | tuple[str, ...]) -> str:
    if isinstance(crate_versions, str):
        crate_versions = (crate_versions,)
    packages = [
        {
            "id": f"registry+https://example.invalid#{_CRATE}@{version}",
            "name": _CRATE,
            "version": version,
        }
        for version in crate_versions
    ]
    return json.dumps(
        {
            "packages": packages,
            "resolve": {"nodes": [{"id": package["id"]} for package in packages]},
        }
    )


def test_cargo_registry_versions_follows_seek_pagination() -> None:
    responses = [
        {
            "versions": [
                {"num": "1.5.7", "yanked": False},
                {"num": "1.5.6", "yanked": True},
            ],
            "meta": {
                "next_page": "?per_page=100&seek=WyIxLjUuNiIsMTIzXQ==",
            },
        },
        {
            "versions": [{"num": "1.5.5", "yanked": False}],
            "meta": {"next_page": None},
        },
    ]
    requests: list[tuple[str, str, dict[str, str]]] = []

    class FakeResponse:
        status = 200

        def __init__(self, document):
            self.document = document

        def read(self):
            return json.dumps(self.document).encode("utf-8")

    class FakeConnection:
        def __init__(self, host, timeout):
            assert host == "crates.io"
            assert timeout > 0

        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return FakeResponse(responses.pop(0))

        def close(self):
            pass

    with patch(_REGISTRY_CONNECTION, side_effect=FakeConnection):
        versions = _cargo_registry_versions(_CRATE)

    assert [method for method, _, _ in requests] == ["GET", "GET"]
    assert requests[0][1] == "/api/v1/crates/serde/versions?per_page=100"
    assert requests[1][1] == (
        "/api/v1/crates/serde/versions?per_page=100&seek=WyIxLjUuNiIsMTIzXQ=="
    )
    assert "?page=" not in requests[0][1]
    assert "?page=" not in requests[1][1]
    assert requests[0][2]["User-Agent"] == "repo-ops"
    assert set(versions) == {"1.5.7", "1.5.5"}


def test_check_rust_filters_change_past_ceiling(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(stdout="    Updating serde v1.2.0 -> v2.0.0\n"),
        ),
    ):
        assert check_rust(ws) == []


def test_check_rust_matches_rust_ecosystem_hold(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")

    with (
        patch(
            _GLOBAL_HOLDS_CACHE,
            return_value=(_held_rule(ecosystem="rust"),),
        ),
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(stdout="    Updating serde v1.2.0 -> v2.0.0\n"),
        ),
    ):
        assert check_rust(ws) == []


def test_check_rust_keeps_change_within_ceiling(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
    expected = "    Updating serde v1.2.0 -> v1.4.0\n"

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_rust.rust.run", return_value=make_proc(stdout=expected)),
    ):
        changes = check_rust(ws)

    assert len(changes) == 1
    assert changes[0].name == _CRATE
    assert changes[0].from_ver == "1.2.0"
    assert changes[0].to == "1.4.0"


def test_apply_rust_clamps_resolved_crate_above_ceiling(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
    versions = ["1.8.0", "1.5.7"]
    clamp_calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["cargo", "update"]:
            return make_proc()
        if cmd == ["cargo", "metadata", "--format-version", "1"]:
            return make_proc(stdout=_metadata(tuple(versions)))
        if cmd == [
            "cargo",
            "update",
            "-p",
            f"{_CRATE}@1.8.0",
            "--precise",
            "1.5.7",
        ]:
            clamp_calls.append(cmd)
            versions[:] = ["1.5.7"]
            return make_proc()
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch(
            _GLOBAL_HOLDS_CACHE,
            return_value=(_held_rule(),),
        ),
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
    ):
        apply_rust(ws)

    assert clamp_calls == [["cargo", "update", "-p", f"{_CRATE}@1.8.0", "--precise", "1.5.7"]]
    assert versions[0] == "1.5.7"


def test_apply_rust_uses_highest_published_prefix_version(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
    versions = ["1.8.0"]
    clamp_calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["cargo", "update"]:
            return make_proc()
        if cmd == ["cargo", "metadata", "--format-version", "1"]:
            return make_proc(stdout=_metadata(tuple(versions)))
        if cmd == [
            "cargo",
            "update",
            "-p",
            f"{_CRATE}@1.8.0",
            "--precise",
            "1.5.7",
        ]:
            clamp_calls.append(cmd)
            versions[:] = ["1.5.7"]
            return make_proc()
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            _REGISTRY_VERSIONS,
            return_value=("1.5.7", "1.5.2", "1.4.9"),
        ),
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
    ):
        apply_rust(ws)

    assert clamp_calls == [["cargo", "update", "-p", f"{_CRATE}@1.8.0", "--precise", "1.5.7"]]
    assert versions[0] == "1.5.7"


def test_apply_rust_skips_unheld_crate(tmp_path, monkeypatch, make_workspace, make_proc) -> None:
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
    versions = ["2.1.0"]
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls.append(cmd)
        if cmd == ["cargo", "update"]:
            return make_proc()
        if cmd == ["cargo", "metadata", "--format-version", "1"]:
            return make_proc(stdout=_metadata(tuple(versions)))
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=()),
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
    ):
        apply_rust(ws)

    assert calls == [
        ["cargo", "update"],
        ["cargo", "metadata", "--format-version", "1"],
    ]
