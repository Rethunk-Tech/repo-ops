"""Yarn conservative vs latest upgrade policy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_js_yarn.yarn import _yarn_up_cmd, apply_yarn
from repo_ops_models import Workspace


def test_yarn_up_cmd_in_range_refresh_by_default(monkeypatch) -> None:
    """#114 — bare `yarn up` is a Yarn 4 no-op; conservative mode must pass
    `-R '*'` to actually re-resolve in-range bumps."""
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    assert _yarn_up_cmd() == ["yarn", "up", "-R", "*"]


def test_yarn_up_cmd_latest_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_YARN_UPDATE_LATEST", "1")
    assert _yarn_up_cmd() == ["yarn", "up", "-R", "*"]


def test_apply_yarn_uses_in_range_refresh_cmd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", side_effect=fake_run),
    ):
        apply_yarn(ws)

    assert calls[0] == ["yarn", "up", "-R", "*"]
    assert calls[1] == ["yarn", "install"]
