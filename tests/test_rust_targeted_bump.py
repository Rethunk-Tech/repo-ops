from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace
from repo_ops_rust.rust import apply_rust, check_rust


def _ws(path: Path) -> Workspace:
    return Workspace(
        path=path,
        ecosystem="rust",
        manager="rust",
        git_repo=path,
        label="crate",
    )


def _proc(
    cmd: list[str],
    returncode: int = 0,
    *,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def test_check_rust_reports_direct_dependency_behind_latest(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[dependencies]\nserde = "^1.0"\n',
        encoding="utf-8",
    )
    ws = _ws(tmp_path)

    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=_proc(["cargo", "update", "--dry-run"]),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
        monkeypatch.context() as env,
    ):
        env.setenv("REPO_OPS_RUST_TARGETED_BUMP", "true")
        result = check_rust(ws)

    assert isinstance(result, tuple)
    changes, behind = result
    assert changes == []
    assert [(change.name, change.from_ver, change.to) for change in behind] == [
        ("serde", "^1.0", "2.0.0")
    ]


def test_check_rust_targeted_bump_defaults_off_without_config(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("REPO_OPS_RUST_TARGETED_BUMP", raising=False)
    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=_proc(["cargo", "update", "--dry-run"]),
        ),
        patch("repo_ops_rust.rust.load_global_config", return_value={}),
    ):
        result = check_rust(_ws(tmp_path))

    assert isinstance(result, list)


def test_check_rust_targeted_bump_reads_repo_config(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / ".repo-ops.toml").write_text(
        "rust_targeted_bump = true\n", encoding="utf-8"
    )
    monkeypatch.delenv("REPO_OPS_RUST_TARGETED_BUMP", raising=False)
    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=_proc(["cargo", "update", "--dry-run"]),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
        patch("repo_ops_rust.rust.load_global_config", return_value={}),
    ):
        result = check_rust(_ws(tmp_path))

    assert isinstance(result, tuple)
    assert result[1][0].name == "serde"


def test_workspace_dependency_targeted_bump_checks_and_rewrites(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = tmp_path / "Cargo.toml"
    lockfile = tmp_path / "Cargo.lock"
    manifest.write_text(
        '[workspace]\nmembers = ["member"]\n\n'
        '[workspace.dependencies]\nserde = "1.0"\n',
        encoding="utf-8",
    )
    lockfile.write_text("serde 1.0.0\n", encoding="utf-8")
    ws = _ws(tmp_path)
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "1")

    with (
        patch(
            "repo_ops_rust.rust.run",
            return_value=_proc(["cargo", "update", "--dry-run"]),
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
        return _proc(list(cmd))

    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        assert apply_rust(ws) == []

    assert 'serde = "2.0.0"' in manifest.read_text(encoding="utf-8")


def test_apply_rust_bumps_manifest_and_precise_updates_lock(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = tmp_path / "Cargo.toml"
    lockfile = tmp_path / "Cargo.lock"
    manifest.write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[dependencies]\nserde = "^1.0"\n',
        encoding="utf-8",
    )
    lockfile.write_text("serde 1.0.0\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls.append(list(cmd))
        if cmd == ["cargo", "update", "-p", "serde", "--precise", "2.0.0"]:
            lockfile.write_text("serde 2.0.0\n", encoding="utf-8")
        return _proc(list(cmd))

    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "1")
        remaining = apply_rust(_ws(tmp_path))

    assert remaining == []
    assert 'serde = "^2.0.0"' in manifest.read_text(encoding="utf-8")
    assert calls == [
        ["cargo", "update"],
        ["cargo", "metadata", "--format-version", "1"],
        ["cargo", "update", "-p", "serde", "--precise", "2.0.0"],
    ]


def test_apply_rust_restores_manifest_and_lock_on_precise_failure(
    tmp_path: Path, monkeypatch
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
            return _proc(list(cmd), 1, stderr="constraint conflict")
        return _proc(list(cmd))

    with (
        patch("repo_ops_rust.rust.run", side_effect=fake_run),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            return_value=("2.0.0",),
        ),
    ):
        monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "1")
        remaining = apply_rust(_ws(tmp_path))

    assert [(change.name, change.to) for change in remaining or []] == [
        ("serde", "2.0.0")
    ]
    assert manifest.read_text(encoding="utf-8") == original_manifest
    assert lockfile.read_text(encoding="utf-8") == original_lock


def test_rust_targeted_bump_skips_non_registry_dependencies(
    tmp_path: Path, monkeypatch
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
            return_value=_proc(["cargo", "update", "--dry-run"]),
        ),
        patch(
            "repo_ops_rust.rust._cargo_registry_versions",
            side_effect=registry,
        ),
    ):
        monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "1")
        result = check_rust(_ws(tmp_path))

    assert result == ([], [])
    assert registry_calls == []
