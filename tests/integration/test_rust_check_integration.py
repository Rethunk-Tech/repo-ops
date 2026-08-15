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
import textwrap

import pytest

from repo_ops_models import Workspace
from repo_ops_rust.rust import check_rust

pytestmark = pytest.mark.integration

_CARGO = shutil.which("cargo")


@pytest.mark.skipif(_CARGO is None, reason="cargo not on PATH")
def test_check_rust_runs_cargo_dry_run(tmp_path) -> None:
    """check_rust completes against a real Cargo.toml without crashing."""
    (tmp_path / "Cargo.toml").write_text(
        textwrap.dedent("""\
            [package]
            name = "rust-integ-test"
            version = "0.1.0"
            edition = "2021"

            [dependencies]
            serde = "=1.0.100"
        """),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    try:
        proc = subprocess.run(
            [_CARGO, "generate-lockfile"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("cargo generate-lockfile timed out — network unavailable")

    if proc.returncode != 0:
        combined = (proc.stdout + proc.stderr).lower()
        if any(
            kw in combined for kw in ("dial", "connect", "network", "timeout", "i/o")
        ):
            pytest.skip(
                f"cargo generate-lockfile failed (network): {proc.stderr[:200]}"
            )
        pytest.fail(f"cargo generate-lockfile failed: {proc.stderr[:400]}")

    ws = Workspace(
        path=tmp_path,
        ecosystem="rust",
        manager="rust",
        git_repo=tmp_path,
        label="rust-integ",
    )
    changes = check_rust(ws)
    assert isinstance(changes, list)
