from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from repo_ops_core.holds import HoldRule
from repo_ops_rust.rust import (
    _cargo_latest_direct_changes,
    apply_rust,
    check_rust,
)


def test_check_rust_reports_direct_dependency_behind_latest(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nserde = "^1.0"\n',
        encoding="utf-8",
    )
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")

    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        result = check_rust(ws)

    assert isinstance(result, tuple)
    changes, behind = result
    assert changes == []
    assert [(change.name, change.from_ver, change.to) for change in behind] == [
        ("serde", "^1.0", "2.0.0")
    ]


def test_cargo_latest_changes_cache_keys_on_manifest_digest(tmp_path: Path, make_workspace) -> None:
    manifest = tmp_path / "Cargo.toml"
    initial = b'[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1.0"\n'
    replacement = initial.replace(b"serde", b"regex")
    manifest.write_bytes(initial)
    original_stat = manifest.stat()

    with patch(
        "repo_ops_rust.rust._cargo_registry_versions",
        return_value=("2.0.0",),
    ):
        first = _cargo_latest_direct_changes(
            make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
        )
        manifest.write_bytes(replacement)
        os.utime(
            manifest,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        replacement_stat = manifest.stat()
        second = _cargo_latest_direct_changes(
            make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
        )

    assert replacement_stat.st_mtime_ns == original_stat.st_mtime_ns
    assert replacement_stat.st_size == original_stat.st_size
    assert [(change.name, change.to) for change in first] == [("serde", "2.0.0")]
    assert [(change.name, change.to) for change in second] == [("regex", "2.0.0")]


def test_workspace_dependency_targeted_bump_checks_and_rewrites(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    manifest = tmp_path / "Cargo.toml"
    lockfile = tmp_path / "Cargo.lock"
    manifest.write_text(
        '[workspace]\nmembers = ["member"]\n\n[workspace.dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    lockfile.write_text("serde 1.0.0\n", encoding="utf-8")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")

    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        result = check_rust(ws)

    assert isinstance(result, tuple)
    assert [(change.name, change.to) for change in result[1]] == [("serde", "2.0.0")]

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["cargo", "update", "-p", "serde", "--precise", "2.0.0"]:
            lockfile.write_text("serde 2.0.0\n", encoding="utf-8")
        return make_proc()

    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        assert apply_rust(ws) == []

    assert 'serde = "2.0.0"' in manifest.read_text(encoding="utf-8")


def test_apply_rust_reuses_check_registry_lookup(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    manifest = tmp_path / "Cargo.toml"
    lockfile = tmp_path / "Cargo.lock"
    manifest.write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    lockfile.write_text("serde 1.0.0\n", encoding="utf-8")
    registry_calls: list[str] = []

    def registry(crate_name: str) -> tuple[str, ...]:
        registry_calls.append(crate_name)
        return ("2.0.0",)

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["cargo", "update", "-p", "serde", "--precise", "2.0.0"]:
            lockfile.write_text("serde 2.0.0\n", encoding="utf-8")
        return make_proc()

    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            side_effect=registry,
        ),
    ):
        result = check_rust(ws)
        assert isinstance(result, tuple)
        assert apply_rust(ws) == []

    assert registry_calls == ["serde"]


def test_held_crate_stays_behind_latest_and_manifest_unchanged(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text("serde 1.0.0\n", encoding="utf-8")
    original_manifest = manifest.read_text(encoding="utf-8")
    hold = HoldRule(package="serde", max_version="1")
    ws = make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")

    with (
        patch("repo_ops_core.holds._global_holds_cached", return_value=(hold,)),
        patch("repo_ops_core.holds._repo_holds_cached", return_value=()),
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        result = check_rust(ws)

    assert isinstance(result, tuple)
    assert [(change.name, change.to) for change in result[1]] == [("serde", "2.0.0")]

    with (
        patch("repo_ops_core.holds._global_holds_cached", return_value=(hold,)),
        patch("repo_ops_core.holds._repo_holds_cached", return_value=()),
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        remaining = apply_rust(ws)

    assert [(change.name, change.to) for change in remaining or []] == [("serde", "2.0.0")]
    assert manifest.read_text(encoding="utf-8") == original_manifest
    assert lockfile.read_text(encoding="utf-8") == "serde 1.0.0\n"


def test_apply_rust_bumps_manifest_and_precise_updates_lock(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    manifest = tmp_path / "Cargo.toml"
    lockfile = tmp_path / "Cargo.lock"
    manifest.write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nserde = "^1.0"\n',
        encoding="utf-8",
    )
    lockfile.write_text("serde 1.0.0\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls.append(list(cmd))
        if cmd == ["cargo", "update", "-p", "serde", "--precise", "2.0.0"]:
            lockfile.write_text("serde 2.0.0\n", encoding="utf-8")
        return make_proc()

    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        remaining = apply_rust(
            make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
        )

    assert remaining == []
    assert 'serde = "^2.0.0"' in manifest.read_text(encoding="utf-8")
    assert calls == [
        ["cargo", "update"],
        ["cargo", "metadata", "--format-version", "1"],
        ["cargo", "update", "-p", "serde", "--precise", "2.0.0"],
    ]


def test_apply_rust_restores_manifest_and_lock_on_precise_failure(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    manifest = tmp_path / "Cargo.toml"
    lockfile = tmp_path / "Cargo.lock"
    original_manifest = (
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[dependencies]\nserde = { version = "~1.0", features = ["derive"] }\n'
    )
    original_lock = "serde 1.0.0\n"
    manifest.write_text(original_manifest, encoding="utf-8")
    lockfile.write_text(original_lock, encoding="utf-8")

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["cargo", "update", "-p", "serde", "--precise", "2.0.0"]:
            return make_proc(1, stderr="constraint conflict")
        return make_proc()

    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        remaining = apply_rust(
            make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
        )

    assert [(change.name, change.to) for change in remaining or []] == [("serde", "2.0.0")]
    assert manifest.read_text(encoding="utf-8") == original_manifest
    assert lockfile.read_text(encoding="utf-8") == original_lock


def test_rust_targeted_bump_skips_non_registry_dependencies(
    tmp_path: Path, monkeypatch, make_workspace, make_proc
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[dependencies]\n"
        'pathdep = { path = "../pathdep", version = "1.0" }\n'
        'gitdep = { git = "https://example.invalid/gitdep", version = "1.0" }\n'
        "workspace_dep = { workspace = true }\n"
        'renamed = { package = "actual", version = "1.0" }\n',
        encoding="utf-8",
    )
    registry_calls: list[str] = []

    def registry(crate_name: str) -> tuple[str, ...]:
        registry_calls.append(crate_name)
        return ("2.0.0",)

    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=make_proc(),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            side_effect=registry,
        ),
    ):
        result = check_rust(
            make_workspace(tmp_path, ecosystem="rust", manager="rust", label="crate")
        )

    assert result == ([], [])
    assert registry_calls == []
