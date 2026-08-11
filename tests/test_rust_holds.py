"""Rust wiring for package version-ceiling ("hold") rules."""

import json
from unittest.mock import patch

import pytest
from repo_ops_core.holds import HoldRule
from repo_ops_models import Workspace
from repo_ops_rust.rust import apply_rust, check_rust

_GLOBAL_HOLDS_CACHE = "repo_ops_rust.rust._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_rust.rust._repo_holds_cached"
_CRATE = "serde"


def _ws(tmp_path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="rust",
        manager="cargo",
        git_repo=tmp_path,
        label="crate",
    )


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _CRATE, "max_version": "1.5"}
    defaults.update(overrides)
    return HoldRule(**defaults)


@pytest.fixture(autouse=True)
def _no_repo_holds():
    with patch(_REPO_HOLDS_CACHE, return_value=()):
        yield


def _proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return type(
        "Proc", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr}
    )()


def _metadata(crate_version: str) -> str:
    package_id = f"registry+https://example.invalid#{_CRATE}@{crate_version}"
    return json.dumps(
        {
            "packages": [{"id": package_id, "name": _CRATE, "version": crate_version}],
            "resolve": {"nodes": [{"id": package_id}]},
        }
    )


def test_check_rust_filters_change_past_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_rust.rust.run",
            return_value=_proc("    Updating serde v1.2.0 -> v2.0.0\n"),
        ),
    ):
        assert check_rust(ws) == []


def test_check_rust_keeps_change_within_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    expected = "    Updating serde v1.2.0 -> v1.4.0\n"

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_rust.rust.run", return_value=_proc(expected)),
    ):
        changes = check_rust(ws)

    assert len(changes) == 1
    assert changes[0].name == _CRATE
    assert changes[0].from_ver == "1.2.0"
    assert changes[0].to == "1.4.0"


def test_apply_rust_clamps_resolved_crate_above_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    versions = {_CRATE: "1.8.0"}
    clamp_calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["cargo", "update"]:
            return _proc()
        if cmd == ["cargo", "metadata", "--format-version", "1"]:
            return _proc(_metadata(versions[_CRATE]))
        if cmd == ["cargo", "update", "-p", _CRATE, "--precise", "1.5.0"]:
            clamp_calls.append(cmd)
            versions[_CRATE] = "1.5.0"
            return _proc()
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
    ):
        apply_rust(ws)

    assert clamp_calls == [["cargo", "update", "-p", _CRATE, "--precise", "1.5.0"]]
    assert versions[_CRATE] == "1.5.0"
