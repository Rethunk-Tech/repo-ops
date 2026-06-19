"""#64 — uv/rust dry-run checks must pass TIMEOUT_CHECK, not run()'s 300s default."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.constants import resolve_timeout_check
from repo_ops_models import Workspace
from repo_ops_py_uv.uv import check_uv
from repo_ops_rust.rust import check_rust


def _ws(tmp_path: Path, ecosystem: str, manager: str) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem=ecosystem,
        manager=manager,
        git_repo=tmp_path,
        label="demo",
    )


@pytest.mark.parametrize(
    "target, check, ecosystem, manager",
    [
        ("repo_ops_py.uv_lock.run", check_uv, "py", "uv"),
        ("repo_ops_rust.rust.run", check_rust, "rust", "cargo"),
    ],
)
def test_dry_run_check_uses_timeout_check(
    tmp_path: Path, target: str, check, ecosystem: str, manager: str
) -> None:
    captured: list[dict] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch(target, side_effect=fake_run):
        check(_ws(tmp_path, ecosystem, manager))

    assert len(captured) == 1
    assert captured[0]["timeout"] == resolve_timeout_check()
