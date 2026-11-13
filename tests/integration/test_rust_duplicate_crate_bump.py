"""Integration: a major bump lands when the lockfile holds two majors of the crate.

``cargo update -p itoa`` is ambiguous with itoa 0.4 and 1.x both locked, and
the same crate listed under two requirement strings must move together or the
resolve still needs the old major. Talks to crates.io; skipped when offline.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from repo_ops_models import Workspace
from repo_ops_rust.rust import apply_rust

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not on PATH"),
]

_ROOT = """[package]
name = "root"
version = "0.1.0"
edition = "2021"

[workspace]
members = ["b"]

[dependencies]
itoa = "0.4.8"
b = { path = "b" }

[dev-dependencies]
itoa = "0.4"
"""
_MEMBER = (
    '[package]\nname = "b"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\nitoa = "1"\n'
)


def test_bump_lands_beside_a_second_locked_major(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "b" / "src").mkdir(parents=True)
    (tmp_path / "Cargo.toml").write_text(_ROOT)
    (tmp_path / "src" / "lib.rs").write_text("")
    (tmp_path / "b" / "Cargo.toml").write_text(_MEMBER)
    (tmp_path / "b" / "src" / "lib.rs").write_text("")
    gen = subprocess.run(
        ["cargo", "generate-lockfile"], cwd=tmp_path, capture_output=True, text=True
    )
    if gen.returncode != 0:
        pytest.skip(f"cargo generate-lockfile failed (offline?): {gen.stderr[-200:]}")

    remaining = apply_rust(
        Workspace(path=tmp_path, ecosystem="rust", manager="rust", git_repo=tmp_path, label=".")
    )

    assert [c.name for c in remaining or []] == []
    manifest = tomllib.loads((tmp_path / "Cargo.toml").read_text())
    assert manifest["dependencies"]["itoa"].startswith("1.")
    assert manifest["dev-dependencies"]["itoa"].startswith("1.")
    locked = subprocess.run(
        ["cargo", "metadata", "--locked", "--format-version", "1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert locked.returncode == 0, locked.stderr
    lock = tomllib.loads((tmp_path / "Cargo.lock").read_text())
    assert [p["version"][:2] for p in lock["package"] if p["name"] == "itoa"] == ["1."]
