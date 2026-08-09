"""Tests for verify gate enhancements (tasks 1-5).

Task 1 — Go: go build ./... runs after go mod verify, before optional go test.
Task 2 — uv: ruff check runs after uv sync --locked when ruff is configured.
Task 3 — Node: REPO_OPS_NODE_VERIFY_SCRIPT overrides the waterfall; empty = skip.
Task 4 — Hatch: _verify_hatch_fast recognises "check"/"typecheck" scripts and
         mypy in dev deps.
Task 5 — Timeouts: TIMEOUT_CHECK/APPLY/VERIFY are env-overridable.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from repo_ops_core.constants import (
    _TIMEOUT_APPLY_ENV,
    _TIMEOUT_CHECK_ENV,
    _TIMEOUT_VERIFY_ENV,
    TIMEOUT_APPLY,
    TIMEOUT_CHECK,
    TIMEOUT_VERIFY,
    resolve_timeout_apply,
    resolve_timeout_check,
    resolve_timeout_verify,
)
from repo_ops_models import Workspace
from repo_ops_upgrade.verify import verify_node, verify_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws(tmp_path: Path, manager: str, ecosystem: str = "go") -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem=ecosystem,
        manager=manager,
        git_repo=tmp_path,
        label="demo",
    )


def _ok(stdout: str = "", stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess([], 0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "err") -> CompletedProcess[str]:
    return CompletedProcess([], 1, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Task 1 — Go: go build ./... gate
# ---------------------------------------------------------------------------


class TestGoVerifyGates:
    def test_go_build_runs_after_mod_verify(self, tmp_path: Path) -> None:
        """go build ./... must be called after go mod verify when both pass."""
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "go")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert calls[0] == ["go", "mod", "verify"]
        assert calls[1] == ["go", "build", "./..."]

    def test_go_mod_verify_failure_short_circuits(self, tmp_path: Path) -> None:
        """go build must NOT run if go mod verify fails."""
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            if cmd[0:3] == ["go", "mod", "verify"]:
                return _fail(stderr="checksum mismatch")
            return _ok()

        ws = _ws(tmp_path, "go")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is not None
        assert "checksum" in result
        assert not any(c == ["go", "build", "./..."] for c in calls)

    def test_go_build_failure_returns_error(self, tmp_path: Path) -> None:
        """go build failure must surface as an error string."""

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            if cmd[0:3] == ["go", "mod", "verify"]:
                return _ok()
            if cmd[0:3] == ["go", "build", "./..."]:
                return _fail(stderr="undefined: foo.Bar")
            return _ok()

        ws = _ws(tmp_path, "go")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is not None
        assert "undefined" in result

    def test_go_test_runs_after_build_when_verify_test(self, tmp_path: Path) -> None:
        """go test ./... must run after go build when verify_test=True."""
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "go")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=True)

        assert result is None
        cmd_strs = [" ".join(c) for c in calls]
        assert "go mod verify" in cmd_strs
        assert "go build ./..." in cmd_strs
        assert "go test ./..." in cmd_strs
        # Order: mod verify → build → test
        assert cmd_strs.index("go mod verify") < cmd_strs.index("go build ./...")
        assert cmd_strs.index("go build ./...") < cmd_strs.index("go test ./...")

    def test_go_build_skipped_when_mod_verify_fails(self, tmp_path: Path) -> None:
        """go test must NOT run if go build fails."""
        called_test = False

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            nonlocal called_test
            if cmd[0:3] == ["go", "test"]:
                called_test = True
            if cmd[0:3] == ["go", "build"]:
                return _fail(stderr="build error")
            return _ok()

        ws = _ws(tmp_path, "go")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws, verify_test=True)

        assert not called_test


class TestRustVerifyGates:
    def test_rust_test_runs_after_check_when_verify_test(self, tmp_path: Path) -> None:
        """cargo test must run after cargo check when verify_test=True."""
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "rust")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=True)

        assert result is None
        cmd_strs = [" ".join(c) for c in calls]
        assert "cargo check" in cmd_strs
        assert "cargo test" in cmd_strs
        assert cmd_strs.index("cargo check") < cmd_strs.index("cargo test")


# ---------------------------------------------------------------------------
# Task 2 — uv: ruff check after uv sync --locked
# ---------------------------------------------------------------------------


class TestUvRuffGate:
    def _write_pyproject(self, path: Path, content: str) -> None:
        (path / "pyproject.toml").write_text(content, encoding="utf-8")

    def test_hatch_fast_replaces_tool_ruff_only_gate(self, tmp_path: Path) -> None:
        """uv uses the shared fast stack instead of a uv-only ruff branch."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "ruff check ."\n'
            "[tool.ruff]\nline-length = 100\n",
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "uv", "python")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert ["uv", "run", "check"] in calls
        assert not any(cmd[:4] == ["uv", "run", "ruff", "check"] for cmd in calls)

    def test_ruff_runs_when_ruff_in_dev_deps(self, tmp_path: Path) -> None:
        """ruff check must run when ruff appears in uv dev-dependencies."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "uv", "python")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(cmd[:4] == ["uv", "run", "ruff", "check"] for cmd in calls)

    def test_ruff_skipped_when_not_configured(self, tmp_path: Path) -> None:
        """ruff check must NOT run when ruff is absent from pyproject.toml."""
        self._write_pyproject(tmp_path, '[project]\nname = "myapp"\n')
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "uv", "python")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert not any(cmd[:4] == ["uv", "run", "ruff", "check"] for cmd in calls)

    def test_ruff_failure_returns_error(self, tmp_path: Path) -> None:
        """ruff check failure must surface as an error string."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n',
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            if cmd[:4] == ["uv", "run", "ruff", "check"]:
                return CompletedProcess([], 1, stdout="Found 3 errors.\n", stderr="")
            return _ok()

        ws = _ws(tmp_path, "uv", "python")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is not None
        assert "error" in result.lower() or "ruff" in result.lower()

    def test_uv_sync_failure_short_circuits_ruff(self, tmp_path: Path) -> None:
        """ruff check must NOT run when uv sync --locked fails."""
        self._write_pyproject(tmp_path, "[tool.ruff]\n")
        called_ruff = False

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            nonlocal called_ruff
            if cmd[:4] == ["uv", "run", "ruff", "check"]:
                called_ruff = True
            if cmd == ["uv", "sync", "--locked"]:
                return _fail(stderr="lock file mismatch")
            return _ok()

        ws = _ws(tmp_path, "uv", "python")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws, verify_test=False)

        assert not called_ruff


class TestPythonManagerFastGates:
    @pytest.mark.parametrize(
        ("manager", "lock_cmd"),
        [
            ("uv", ["uv", "sync", "--locked"]),
            ("poetry", ["poetry", "check"]),
            ("pdm", ["pdm", "sync", "--clean"]),
            ("pipenv", ["pipenv", "verify"]),
        ],
    )
    def test_lock_gate_then_hatch_fast(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, manager, "python")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert calls == [lock_cmd, ["uv", "run", "check"]]

    @pytest.mark.parametrize(
        ("manager", "lock_cmd"),
        [
            ("uv", ["uv", "sync", "--locked"]),
            ("poetry", ["poetry", "check"]),
            ("pdm", ["pdm", "sync", "--clean"]),
            ("pipenv", ["pipenv", "verify"]),
        ],
    )
    def test_lock_gate_failure_blocks_hatch_fast(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _fail(stderr="lock gate failed") if cmd == lock_cmd else _ok()

        ws = _ws(tmp_path, manager, "python")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws, verify_test=False)

        assert result is not None
        assert "failed" in result
        assert calls == [lock_cmd]

    @pytest.mark.parametrize(
        ("manager", "binary"),
        [
            ("uv", "uv"),
            ("poetry", "poetry"),
            ("pdm", "pdm"),
            ("pipenv", "pipenv"),
        ],
    )
    def test_missing_manager_binary_fails_closed(
        self,
        tmp_path: Path,
        manager: str,
        binary: str,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )
        ws = _ws(tmp_path, manager, "python")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value=None),
            patch("repo_ops_upgrade.verify.run") as run_mock,
        ):
            result = verify_workspace(ws, verify_test=False)

        assert result == f"{binary} not on PATH"
        run_mock.assert_not_called()

    @pytest.mark.parametrize(
        ("manager", "lock_cmd"),
        [
            ("uv", ["uv", "sync", "--locked"]),
            ("poetry", ["poetry", "check"]),
            ("pdm", ["pdm", "sync", "--clean"]),
            ("pipenv", ["pipenv", "verify"]),
        ],
    )
    def test_hatch_fast_failure_blocks(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            if cmd == ["uv", "run", "check"]:
                return _fail(stderr="fast gate failed")
            return _ok()

        ws = _ws(tmp_path, manager, "python")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws, verify_test=False)

        assert result is not None
        assert "fast gate failed" in result


# ---------------------------------------------------------------------------
# Task 3 — Node: REPO_OPS_NODE_VERIFY_SCRIPT override
# ---------------------------------------------------------------------------


class TestNodeVerifyScriptEnv:
    def _write_pkg(self, path: Path, scripts: dict[str, str] | None = None) -> None:
        import json

        content = {"scripts": scripts or {"ci": "echo ci", "build": "echo build"}}
        (path / "package.json").write_text(json.dumps(content), encoding="utf-8")

    def test_env_override_uses_specified_script(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REPO_OPS_NODE_VERIFY_SCRIPT=custom → use 'custom', not the waterfall."""
        self._write_pkg(tmp_path, {"ci": "echo ci", "custom": "echo custom"})
        monkeypatch.setenv("REPO_OPS_NODE_VERIFY_SCRIPT", "custom")
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "yarn", "node")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is None
        script_calls = [c for c in calls if "custom" in c]
        assert script_calls, f"expected 'custom' script call, got: {calls}"

    def test_env_override_empty_string_skips_script_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REPO_OPS_NODE_VERIFY_SCRIPT='' → skip script step (immutable install still runs)."""
        self._write_pkg(tmp_path, {"ci": "echo ci"})
        monkeypatch.setenv("REPO_OPS_NODE_VERIFY_SCRIPT", "")
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "npm", "node")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is None
        # npm ci (immutable install) must still have run
        assert any("npm" in c and c[-1] == "ci" for c in calls), (
            f"npm ci not called: {calls}"
        )
        # But no package.json script should have been run
        assert not any("echo" in str(c) for c in calls)

    def test_env_unset_uses_waterfall(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When env var absent, first matching waterfall candidate is used."""
        self._write_pkg(tmp_path, {"build": "echo build"})
        monkeypatch.delenv("REPO_OPS_NODE_VERIFY_SCRIPT", raising=False)
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "npm", "node")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is None
        # 'build' is in the waterfall; ci/ci:pushgate are not defined → build used
        assert any("build" in c for c in calls)

    def test_env_override_script_failure_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REPO_OPS_NODE_VERIFY_SCRIPT failure must surface as an error."""
        self._write_pkg(tmp_path, {"myscript": "echo ok"})
        monkeypatch.setenv("REPO_OPS_NODE_VERIFY_SCRIPT", "myscript")

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            if "myscript" in cmd:
                return _fail(stderr="script failed")
            return _ok()

        ws = _ws(tmp_path, "npm", "node")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is not None

    def test_waterfall_first_match_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Waterfall picks 'ci' over 'build' when both exist."""
        self._write_pkg(
            tmp_path, {"ci": "echo ci", "build": "echo build", "lint": "echo lint"}
        )
        monkeypatch.delenv("REPO_OPS_NODE_VERIFY_SCRIPT", raising=False)
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = _ws(tmp_path, "npm", "node")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_node(ws)

        # Only 'ci' should have been run as the script step, not 'build' or 'lint'
        script_step_calls = [
            c for c in calls if len(c) > 1 and c[-1] in ("ci", "build", "lint")
        ]
        assert script_step_calls[0][-1] == "ci"
        assert len([c for c in script_step_calls if c[-1] in ("build", "lint")]) == 0


# ---------------------------------------------------------------------------
# Task 4 — Hatch: _verify_hatch_fast extended heuristics
# ---------------------------------------------------------------------------


class TestVerifyHatchFast:
    def _write_pyproject(self, path: Path, content: str) -> None:
        (path / "pyproject.toml").write_text(content, encoding="utf-8")

    def _hatch_ws(self, tmp_path: Path) -> Workspace:
        return _ws(tmp_path, "hatch", "python")

    def test_check_script_invoked(self, tmp_path: Path) -> None:
        """'check' script in [project.scripts] must be invoked."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.scripts]\ncheck = "mypy src"\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "check"] for c in calls)

    def test_typecheck_script_invoked(self, tmp_path: Path) -> None:
        """'typecheck' script must be invoked when present."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.scripts]\ntypecheck = "pyright"\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "typecheck"] for c in calls)

    def test_mypy_in_dev_deps_invoked(self, tmp_path: Path) -> None:
        """mypy in dev deps triggers 'uv run mypy .' when no higher-priority heuristic matches."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["mypy>=1.0"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "mypy", "."] for c in calls)

    def test_mypy_skipped_when_test_script_present(self, tmp_path: Path) -> None:
        """'test' script takes priority over mypy heuristic."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.scripts]\ntest = "pytest"\n'
            '[tool.uv]\ndev-dependencies = ["mypy>=1.0"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "test"] for c in calls)
        assert not any(c == ["uv", "run", "mypy", "."] for c in calls)

    def test_check_script_failure_returns_error(self, tmp_path: Path) -> None:
        """check script failure must surface an error string."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.scripts]\ncheck = "mypy src"\n',
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            if cmd == ["uv", "run", "check"]:
                return _fail(stderr="type error found")
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is not None
        assert "check" in result or "type" in result

    def test_hatch_env_deps_mypy_recognised(self, tmp_path: Path) -> None:
        """mypy in hatch env dependencies is still recognised as a dev dep."""
        self._write_pyproject(
            tmp_path,
            '[tool.hatch.envs.default]\ndependencies = ["mypy"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "mypy", "."] for c in calls)

    def test_pyrefly_in_dev_deps_invoked(self, tmp_path: Path) -> None:
        """pyrefly in dev deps triggers 'uv run pyrefly check .' when no higher-priority heuristic matches."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["pyrefly>=0.1"]\n'
            '[tool.pyrefly]\nproject-excludes = ["tests/**", "scripts/**"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(
            c
            == [
                "uv",
                "run",
                "pyrefly",
                "check",
                ".",
                "--project-excludes",
                "tests/**",
                "scripts/**",
            ]
            for c in calls
        )

    def test_hatch_env_deps_pyrefly_recognised(self, tmp_path: Path) -> None:
        """pyrefly in hatch env dependencies is still recognised as a dev dep."""
        self._write_pyproject(
            tmp_path,
            '[tool.hatch.envs.default]\ndependencies = ["pyrefly"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "pyrefly", "check", "."] for c in calls)

    def test_pyrefly_skipped_when_test_script_present(self, tmp_path: Path) -> None:
        """'test' script takes priority over pyrefly heuristic."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.scripts]\ntest = "pytest"\n'
            '[tool.uv]\ndev-dependencies = ["pyrefly>=0.1"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "test"] for c in calls)
        assert not any(c == ["uv", "run", "pyrefly", "check", "."] for c in calls)

    def test_mypy_takes_priority_over_pyrefly(self, tmp_path: Path) -> None:
        """When both mypy and pyrefly are present in dev deps, the mypy branch wins (checked first)."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["mypy>=1.0", "pyrefly>=0.1"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _ok()

        ws = self._hatch_ws(tmp_path)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws, verify_test=False)

        assert result is None
        assert any(c == ["uv", "run", "mypy", "."] for c in calls)
        assert not any(c == ["uv", "run", "pyrefly", "check", "."] for c in calls)


# ---------------------------------------------------------------------------
# Task 5 — Env-overridable timeouts
# ---------------------------------------------------------------------------


class TestTimeoutEnvOverride:
    def test_resolve_timeout_check_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_TIMEOUT_CHECK_ENV, raising=False)
        assert resolve_timeout_check() == TIMEOUT_CHECK

    def test_resolve_timeout_apply_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_TIMEOUT_APPLY_ENV, raising=False)
        assert resolve_timeout_apply() == TIMEOUT_APPLY

    def test_resolve_timeout_verify_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_TIMEOUT_VERIFY_ENV, raising=False)
        assert resolve_timeout_verify() == TIMEOUT_VERIFY

    def test_resolve_timeout_check_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_TIMEOUT_CHECK_ENV, "42")
        assert resolve_timeout_check() == 42

    def test_resolve_timeout_apply_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_TIMEOUT_APPLY_ENV, "123")
        assert resolve_timeout_apply() == 123

    def test_resolve_timeout_verify_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_TIMEOUT_VERIFY_ENV, "300")
        assert resolve_timeout_verify() == 300

    def test_invalid_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_TIMEOUT_VERIFY_ENV, "not_a_number")
        assert resolve_timeout_verify() == TIMEOUT_VERIFY

    def test_zero_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_TIMEOUT_VERIFY_ENV, "0")
        assert resolve_timeout_verify() == TIMEOUT_VERIFY

    def test_negative_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_TIMEOUT_CHECK_ENV, "-10")
        assert resolve_timeout_check() == TIMEOUT_CHECK

    def test_float_env_truncated_to_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_TIMEOUT_VERIFY_ENV, "45.9")
        assert resolve_timeout_verify() == 45

    def test_verify_workspace_go_uses_resolved_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """verify_workspace (go) must pass the resolved verify timeout to go build."""
        monkeypatch.setenv(_TIMEOUT_VERIFY_ENV, "77")
        captured: list[dict] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            captured.append({"cmd": cmd, "timeout": timeout})
            return _ok()

        ws = _ws(tmp_path, "go")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws, verify_test=False)

        build_call = next(c for c in captured if c["cmd"] == ["go", "build", "./..."])
        assert build_call["timeout"] == 77

    def test_verify_workspace_uv_ruff_uses_resolved_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """verify_workspace (uv) ruff step must use the resolved verify timeout."""
        monkeypatch.setenv(_TIMEOUT_VERIFY_ENV, "55")
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n',
            encoding="utf-8",
        )
        captured: list[dict] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None
        ) -> CompletedProcess[str]:
            captured.append({"cmd": cmd, "timeout": timeout})
            return _ok()

        ws = _ws(tmp_path, "uv", "python")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws, verify_test=False)

        ruff_call = next(
            (c for c in captured if c["cmd"][:4] == ["uv", "run", "ruff", "check"]),
            None,
        )
        assert ruff_call is not None
        assert ruff_call["timeout"] == 55
