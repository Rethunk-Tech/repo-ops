"""Tests for per-repo verify profiles and .repo-ops.toml sidecar.

Covers:
- .repo-ops.toml read (flat keys)
- verify.script honored (config override of the Node waterfall)
- verify.skip short-circuits verify gate entirely
- verify.timeout passed to run()
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_core.config import load_repo_config
from repo_ops_upgrade.verify import verify_node, verify_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout: str = "", stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess([], 0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "err") -> CompletedProcess[str]:
    return CompletedProcess([], 1, stdout=stdout, stderr=stderr)


def _write_pkg(path: Path, scripts: dict[str, str] | None = None) -> None:
    content = {"scripts": scripts or {"ci": "echo ci"}}
    (path / "package.json").write_text(json.dumps(content), encoding="utf-8")


# ---------------------------------------------------------------------------
# .repo-ops.toml sidecar read
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
class TestDotRepoOpsToml:
    def test_reads_flat_keys(self, tmp_path: Path) -> None:
        (tmp_path / ".repo-ops.toml").write_text('exclude = ["vendor"]\n', encoding="utf-8")
        cfg = load_repo_config(tmp_path)
        assert cfg.get("exclude") == ["vendor"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_repo_config(tmp_path) == {}

    def test_reads_verify_subtable(self, tmp_path: Path) -> None:
        (tmp_path / ".repo-ops.toml").write_text(
            "[verify]\nscript = 'build'\ntimeout = 60\nskip = false\n",
            encoding="utf-8",
        )
        cfg = load_repo_config(tmp_path)
        verify = cfg.get("verify", {})
        assert verify.get("script") == "build"
        assert verify.get("timeout") == 60
        assert verify.get("skip") is False

    def test_invalid_toml_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".repo-ops.toml").write_text("not valid toml ][[\n", encoding="utf-8")
        cfg = load_repo_config(tmp_path)
        assert cfg == {}


# ---------------------------------------------------------------------------
# pyproject wins over .repo-ops.toml (per-key merge)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# verify.script honored
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
class TestVerifyScriptFromConfig:
    def test_config_script_used_instead_of_waterfall(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace, fake_run: Any
    ) -> None:
        """verify.script in config overrides the waterfall when env var absent."""
        _write_pkg(tmp_path, {"ci": "echo ci", "build:prod": "echo build"})
        (tmp_path / ".repo-ops.toml").write_text(
            '[verify]\nscript = "build:prod"\n', encoding="utf-8"
        )

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is None
        assert any("build:prod" in c for c in fake_run.calls), (
            f"expected build:prod in {fake_run.calls}"
        )
        # waterfall 'ci' must NOT have been called as a script-run step
        # (npm ci = immutable install; npm run ci = waterfall script — check for the latter)
        assert not any("run" in c and c[-1] == "ci" for c in fake_run.calls), (
            f"waterfall script 'ci' must not run when config overrides: {fake_run.calls}"
        )

    def test_config_script_empty_string_skips_script_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace, fake_run: Any
    ) -> None:
        """verify.script = '' in config skips the script step (immutable install still runs)."""
        _write_pkg(tmp_path, {"ci": "echo ci"})
        (tmp_path / ".repo-ops.toml").write_text('[verify]\nscript = ""\n', encoding="utf-8")

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is None
        # npm ci (immutable) still ran
        assert any("npm" in c and c[-1] == "ci" for c in fake_run.calls)
        # No package.json script was invoked
        assert not any("echo" in str(c) for c in fake_run.calls)

    def test_config_script_failure_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace
    ) -> None:
        _write_pkg(tmp_path, {"gate": "echo gate"})
        (tmp_path / ".repo-ops.toml").write_text('[verify]\nscript = "gate"\n', encoding="utf-8")

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            if "gate" in cmd:
                return _fail(stderr="gate failed")
            return _ok()

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is not None
        assert "gate" in result or "failed" in result


# ---------------------------------------------------------------------------
# verify.skip short-circuits verify_workspace
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
class TestVerifySkip:
    def test_skip_true_returns_none(self, tmp_path: Path, make_workspace) -> None:
        (tmp_path / ".repo-ops.toml").write_text("[verify]\nskip = true\n", encoding="utf-8")
        called: list[bool] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            called.append(True)
            return _ok()

        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        # skip=true is not a verify failure: returns None so orchestration
        # does not mark the workspace failed or trigger a revert.
        assert result is None
        assert not called, "run() must not be called when skip=true"

    def test_skip_false_runs_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace, fake_run: Any
    ) -> None:
        (tmp_path / ".repo-ops.toml").write_text("[verify]\nskip = false\n", encoding="utf-8")
        _write_pkg(tmp_path, {"ci": "echo ci"})

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls, "expected at least one run() call"

    def test_skip_absent_runs_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace, fake_run: Any
    ) -> None:
        _write_pkg(tmp_path, {"ci": "echo ci"})
        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")

        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls

    def test_workspace_sidecar_skips_only_that_workspace(
        self, tmp_path: Path, make_workspace
    ) -> None:
        broken = tmp_path / "fixtures" / "broken"
        clean = tmp_path / "fixtures" / "clean"
        broken.mkdir(parents=True)
        clean.mkdir(parents=True)
        (broken / ".repo-ops.toml").write_text("[verify]\nskip = true\n", encoding="utf-8")
        calls: list[Path] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cwd)
            return _ok()

        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            for path in (broken, clean):
                ws = make_workspace(path, git_repo=tmp_path, manager="go", ecosystem="go")
                assert verify_workspace(ws) is None

        assert broken not in calls
        assert clean in calls


# ---------------------------------------------------------------------------
# verify.timeout passed to run()
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib required")
class TestVerifyTimeout:
    def test_config_timeout_passed_to_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace
    ) -> None:
        (tmp_path / ".repo-ops.toml").write_text("[verify]\ntimeout = 42\n", encoding="utf-8")
        captured: list[dict] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            captured.append({"cmd": cmd, "timeout": timeout})
            return _ok()

        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws)

        build_call = next((c for c in captured if c["cmd"] == ["go", "build", "./..."]), None)
        assert build_call is not None
        assert build_call["timeout"] == 42

    def test_node_config_timeout_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace
    ) -> None:
        (tmp_path / ".repo-ops.toml").write_text(
            '[verify]\ntimeout = 77\nscript = "build"\n', encoding="utf-8"
        )
        _write_pkg(tmp_path, {"build": "echo build"})
        captured: list[dict] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            captured.append({"cmd": cmd, "timeout": timeout})
            return _ok()

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_node(ws)

        # All calls (npm ci + script) must use the configured timeout.
        assert all(c["timeout"] == 77 for c in captured), (
            f"expected all timeouts=77, got {captured}"
        )
