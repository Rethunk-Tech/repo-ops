"""uv/rust dry-run checks must pass TIMEOUT_CHECK, not run()'s 300s default."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.constants import resolve_timeout_check
from repo_ops_py_uv.uv import check_uv
from repo_ops_rust.rust import check_rust


@pytest.mark.parametrize(
    "target, check, ecosystem, manager",
    [
        ("repo_ops_py.uv_lock.run", check_uv, "py", "uv"),
        ("repo_ops_rust.rust.run", check_rust, "rust", "cargo"),
    ],
)
def test_dry_run_check_uses_timeout_check(
    tmp_path: Path,
    target: str,
    check,
    ecosystem: str,
    manager: str,
    make_workspace,
    monkeypatch,
) -> None:
    # Targeted bump defaults on and, for rust, would read a Cargo.toml
    # this fixture never creates — irrelevant to what this test checks
    # (the timeout passed through to run()), so pin it off here.
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    captured: list[dict] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch(target, side_effect=fake_run):
        check(make_workspace(tmp_path, ecosystem=ecosystem, manager=manager, label="demo"))

    assert len(captured) == 1
    assert captured[0]["timeout"] == resolve_timeout_check()
