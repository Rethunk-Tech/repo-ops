"""Regression tests for ``check_bun`` outdated parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_bun.bun import check_bun
from repo_ops_models import Workspace


def _ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="bun",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def test_check_bun_prefers_json() -> None:
    payload = {"vite": {"current": "5.0.0", "latest": "6.0.0"}}
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        if "--json" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            )
        raise AssertionError("text outdated should not run when JSON succeeds")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes = check_bun(_ws())

    assert calls == [["bun", "outdated", "--json"]]
    assert len(changes) == 1
    assert changes[0].name == "vite"
    assert changes[0].from_ver == "5.0.0"
    assert changes[0].to == "6.0.0"


def test_check_bun_filters_table_artifacts_from_text() -> None:
    table_stdout = "\n".join(
        [
            "Package Current Latest",
            "─".join("─" for _ in range(10)),
            "| Package → |",
            "vite 5.0.0 6.0.0",
        ]
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        if "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=table_stdout, stderr="")

    with patch("repo_ops_js_bun.bun.run", fake_run):
        changes = check_bun(_ws())

    assert [c.name for c in changes] == ["vite"]
