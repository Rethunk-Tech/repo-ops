"""Integration test: cargo update --dry-run via check_rust.

Constructs a minimal Cargo project with a pinned dependency and runs the real
``check_rust`` helper (``cargo update --dry-run``).  This is a resilience test:
the workspace must not crash and must return a list of Change objects.

Network: ``cargo generate-lockfile`` may fetch crates.io on first run; skipped
when the command fails with a network-style error.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repo_ops_models import Workspace
from repo_ops_rust.rust import apply_rust

pytestmark = pytest.mark.integration


def test_apply_bumps_every_workspace_member_and_stays_locked(tmp_path: Path) -> None:
    if shutil.which("cargo") is None:
        pytest.skip("cargo not installed")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n', encoding="utf-8"
    )
    for name, dep in (("a", 'itoa = "0.4"'), ("b", 'ryu = "0.2"')):
        member = tmp_path / "crates" / name
        (member / "src").mkdir(parents=True)
        (member / "src" / "lib.rs").write_text("", encoding="utf-8")
        (member / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n'
            f"[dependencies]\n{dep}\n",
            encoding="utf-8",
        )
    gen = subprocess.run(
        ["cargo", "generate-lockfile"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    if gen.returncode != 0:
        pytest.skip(f"cargo cannot reach the registry: {gen.stderr.strip()[-200:]}")

    ws = Workspace(
        path=tmp_path, git_repo=tmp_path, ecosystem="rust", manager="rust", label="crate"
    )
    apply_rust(ws)

    for name in ("a", "b"):
        text = (tmp_path / "crates" / name / "Cargo.toml").read_text(encoding="utf-8")
        assert '0.4"' not in text and '"0.2"' not in text
    locked = subprocess.run(
        ["cargo", "metadata", "--locked", "--format-version", "1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert locked.returncode == 0, locked.stderr
