"""Toolchain pin selection, only-raise, go.work, holds and named channels."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from repo_ops_core.holds import clear_hold_caches
from repo_ops_go.go import register_go_work_dir
from repo_ops_models import Workspace
from repo_ops_upgrade import toolchains

GO_JSON = json.dumps(
    [{"version": "go1.28rc1", "stable": False}, {"version": "go1.27.3", "stable": True}]
)
RUST_TOML = '[pkg.cargo]\nversion = "1.0"\n[pkg.rust]\nversion = "1.98.1 (abc 2026-10-01)"\n'
NODE_JSON = json.dumps(
    [
        {"version": "v25.0.0"},
        {"version": "v22.9.1"},
        {"version": "v22.10.0"},
        {"version": "v20.1.0"},
    ]
)
PY_JSON = json.dumps([{"latest": "3.15.0b1"}, {"latest": "3.14.2"}, {"latest": "3.13.9"}])


@pytest.fixture(autouse=True)
def _feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    feeds = {
        "https://go.dev/dl/?mode=json": GO_JSON,
        "https://static.rust-lang.org/dist/channel-rust-stable.toml": RUST_TOML,
        "https://nodejs.org/dist/index.json": NODE_JSON,
        "https://endoflife.date/api/python.json": PY_JSON,
    }
    monkeypatch.setattr(toolchains, "_fetch", feeds.get)
    monkeypatch.setattr(toolchains, "_local_go_version", lambda: "1.27.3")
    # The pin rewrite is under test here; tests/integration covers the real tidy.
    monkeypatch.setattr(toolchains, "tidy_go_module", lambda _ws: None)
    clear_hold_caches()


def _ws(path: Path, ecosystem: str) -> Workspace:
    return Workspace(path=path, ecosystem=ecosystem, manager=ecosystem, git_repo=path, label="x")


def test_go_raises_directive_and_toolchain_keeping_precision(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module m\n\ngo 1.27.2\n\ntoolchain go1.27.2\n")
    changes, behind = toolchains.apply_toolchains(_ws(tmp_path, "go"))
    assert [(c.name, c.from_ver, c.to) for c in changes] == [
        ("go", "1.27.2", "1.27.3"),
        ("go toolchain", "1.27.2", "1.27.3"),
    ]
    assert behind == []
    assert (tmp_path / "go.mod").read_text() == "module m\n\ngo 1.27.3\n\ntoolchain go1.27.3\n"


def test_go_directive_caps_at_local_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A published Go newer than ``go env GOVERSION`` must not raise the directive past local."""
    monkeypatch.setattr(
        toolchains,
        "_fetch",
        lambda url: (
            json.dumps([{"version": "go1.99.0", "stable": True}]) if "go.dev/dl" in url else None
        ),
    )
    (tmp_path / "go.mod").write_text("module m\n\ngo 1.27.2\n")
    changes, behind = toolchains.plan_toolchains(_ws(tmp_path, "go"))
    assert [(c.name, c.from_ver, c.to) for c in changes] == [("go", "1.27.2", "1.27.3")]
    assert behind == []


def test_go_never_lowers_and_two_part_waits_for_a_new_minor(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("go 1.29.0\n")
    assert toolchains.plan_toolchains(_ws(tmp_path, "go")) == ([], [])
    (tmp_path / "go.mod").write_text("go 1.27\n")
    assert toolchains.plan_toolchains(_ws(tmp_path, "go")) == ([], [])


def test_go_work_moves_with_its_module(tmp_path: Path) -> None:
    mod = tmp_path / "svc"
    mod.mkdir()
    (mod / "go.mod").write_text("go 1.27.2\n")
    (tmp_path / "go.work").write_text("go 1.27.2\n\nuse ./svc\n")
    (tmp_path / ".git").mkdir()
    register_go_work_dir(mod, tmp_path)
    # go.work sits above the module but inside the repo, which is the layout it
    # is allowed to write; only a work file outside git_repo is refused.
    ws = Workspace(path=mod, ecosystem="go", manager="go", git_repo=tmp_path, label="x")
    changes, _ = toolchains.apply_toolchains(ws)
    assert {c.name for c in changes} == {"go", "go.work go directive"}
    assert (tmp_path / "go.work").read_text().startswith("go 1.27.3\n")


def test_hold_blocks_bump_and_reports_behind(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("go 1.27.2\n")
    (tmp_path / ".repo-ops.toml").write_text('[[hold]]\npackage = "go"\nmax_version = "1.27.2"\n')
    changes, behind = toolchains.apply_toolchains(_ws(tmp_path, "go"))
    assert changes == []
    assert [(c.name, c.to) for c in behind] == [("go", "1.27.3")]
    assert (tmp_path / "go.mod").read_text() == "go 1.27.2\n"


def test_rust_pin_moves_but_named_channel_and_msrv_do_not(tmp_path: Path) -> None:
    (tmp_path / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.98"\n')
    (tmp_path / "Cargo.toml").write_text('[package]\nrust-version = "1.70"\n')
    assert toolchains.plan_toolchains(_ws(tmp_path, "rust")) == ([], [])
    (tmp_path / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.97.0"\n')
    toolchains.apply_toolchains(_ws(tmp_path, "rust"))
    assert 'channel = "1.98.1"' in (tmp_path / "rust-toolchain.toml").read_text()
    assert "1.70" in (tmp_path / "Cargo.toml").read_text()
    (tmp_path / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "stable"\n')
    assert toolchains.plan_toolchains(_ws(tmp_path, "rust")) == ([], [])


def test_node_stays_within_its_major_and_keeps_v_prefix(tmp_path: Path) -> None:
    (tmp_path / ".nvmrc").write_text("v22.9.0\n")
    toolchains.apply_toolchains(_ws(tmp_path, "node"))
    assert (tmp_path / ".nvmrc").read_text() == "v22.10.0\n"
    (tmp_path / ".node-version").write_text("lts/*\n")
    assert [c.name for c in toolchains.plan_toolchains(_ws(tmp_path, "node"))[0]] == []


def test_python_pin_takes_latest_stable_release(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.13.1\n")
    toolchains.apply_toolchains(_ws(tmp_path, "py"))
    assert (tmp_path / ".python-version").read_text() == "3.14.2\n"


def test_fetch_failure_leaves_pins_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolchains, "_fetch", lambda url: None)
    (tmp_path / "go.mod").write_text("go 1.27.2\n")
    assert toolchains.plan_toolchains(_ws(tmp_path, "go")) == ([], [])


def test_expired_hold_is_lifted(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("go 1.27.2\n")
    until = (date.today() - timedelta(days=1)).isoformat()
    (tmp_path / ".repo-ops.toml").write_text(
        f'[[hold]]\npackage = "go"\nmax_version = "1.27.2"\nuntil = "{until}"\n'
    )
    assert len(toolchains.plan_toolchains(_ws(tmp_path, "go"))[0]) == 1


def test_fetch_returns_body_or_none_on_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.undo()

    class _Body:
        def __enter__(self) -> _Body:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"ok"

    monkeypatch.setattr(toolchains, "open_url", lambda *_a, **_k: _Body())
    toolchains._fetch.cache_clear()
    try:
        assert toolchains._fetch("https://example.invalid/ok") == "ok"
    finally:
        toolchains._fetch.cache_clear()

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError("offline")

    monkeypatch.setattr(toolchains, "open_url", _raise)
    caplog.set_level("WARNING", logger="repo_ops_upgrade.toolchains")
    try:
        assert toolchains._fetch("https://example.invalid/err") is None
    finally:
        toolchains._fetch.cache_clear()
    assert "toolchain lookup failed" in caplog.text


def test_invalid_feed_json_leaves_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolchains, "_fetch", lambda _url: "{not json")
    (tmp_path / "go.mod").write_text("go 1.27.2\n")
    assert toolchains.plan_toolchains(_ws(tmp_path, "go")) == ([], [])


def test_local_go_version_reads_go_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    go = tmp_path / "go"
    monkeypatch.setenv("PATH", str(tmp_path))

    def _version() -> str | None:
        toolchains._local_go_version.cache_clear()
        try:
            return toolchains._local_go_version()
        finally:
            toolchains._local_go_version.cache_clear()

    go.write_text("#!/bin/sh\necho go1.27.3\n")
    go.chmod(0o755)
    assert _version() == "1.27.3"

    go.write_text("#!/bin/sh\nexit 1\n")
    assert _version() is None

    go.write_text("#!/bin/sh\nexit 0\n")
    assert _version() is None

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    assert _version() is None


def test_go_uses_feed_when_local_toolchain_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(toolchains, "_local_go_version", lambda: None)
    (tmp_path / "go.mod").write_text("go 1.27.2\n")
    changes, behind = toolchains.plan_toolchains(_ws(tmp_path, "go"))
    assert [(c.name, c.to) for c in changes] == [("go", "1.27.3")]
    assert behind == []


def test_go_work_outside_repo_refuses_writes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("go 1.27.2\n")
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "go.work").write_text("go 1.27.2\n")
    register_go_work_dir(repo, outside)
    ws = Workspace(path=repo, ecosystem="go", manager="go", git_repo=repo, label="x")
    changes, _behind = toolchains.apply_toolchains(ws)
    assert changes == []
    assert (repo / "go.mod").read_text() == "go 1.27.2\n"
    assert (outside / "go.work").read_text() == "go 1.27.2\n"
