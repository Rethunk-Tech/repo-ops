"""Per-ecosystem verify gates.

- Go: ``go build ./...`` runs after ``go mod verify``, before optional ``go test``.
- uv: ``ruff check`` runs after ``uv sync --locked`` when ruff is configured.
- gate: the gates a project declares outrank the built-in Node waterfall and
  Python ladder, and its absence degrades to them rather than failing.
- Hatch: ``_verify_hatch_fast`` recognises "check"/"typecheck" scripts and mypy
  in dev deps.
- Timeouts: ``TIMEOUT_CHECK``/``APPLY``/``VERIFY`` are env-overridable.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_models import Workspace
from repo_ops_upgrade.verify import verify_node, verify_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout: str = "", stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess([], 0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "err") -> CompletedProcess[str]:
    return CompletedProcess([], 1, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Go: go build ./... gate
# ---------------------------------------------------------------------------


class TestGoVerifyGates:
    def test_go_build_and_test_run_after_mod_verify(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        """go build and go test run after go mod verify when both pass."""

        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        # The gate probe may sit between them; the ordering that matters is
        # that the build gate never precedes the checksum gate.
        assert [c for c in fake_run.calls if c[0] == "go"] == [
            ["go", "mod", "verify"],
            ["go", "build", "./..."],
            ["go", "test", "./..."],
        ]

    def test_go_mod_verify_failure_short_circuits(self, tmp_path: Path, make_workspace) -> None:
        """go build must NOT run if go mod verify fails."""
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            if cmd[0:3] == ["go", "mod", "verify"]:
                return _fail(stderr="checksum mismatch")
            return _ok()

        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is not None
        assert "checksum" in result
        assert not any(c == ["go", "build", "./..."] for c in calls)

    def test_go_build_failure_returns_error(self, tmp_path: Path, make_workspace) -> None:
        """go build failure must surface as an error string."""

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd[0:3] == ["go", "mod", "verify"]:
                return _ok()
            if cmd[0:3] == ["go", "build", "./..."]:
                return _fail(stderr="undefined: foo.Bar")
            return _ok()

        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is not None
        assert "undefined" in result

    def test_go_build_skipped_when_mod_verify_fails(self, tmp_path: Path, make_workspace) -> None:
        """go test must NOT run if go build fails."""
        called_test = False

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            nonlocal called_test
            if cmd[0:3] == ["go", "test"]:
                called_test = True
            if cmd[0:3] == ["go", "build"]:
                return _fail(stderr="build error")
            return _ok()

        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws)

        assert not called_test


class TestRustVerifyGates:
    def test_rust_test_runs_after_check(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        """cargo test runs after cargo check."""

        ws = make_workspace(tmp_path, manager="rust", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        cmd_strs = [" ".join(c) for c in fake_run.calls]
        assert "cargo check" in cmd_strs
        assert "cargo test" in cmd_strs
        assert cmd_strs.index("cargo check") < cmd_strs.index("cargo test")


# ---------------------------------------------------------------------------
# uv: ruff check after uv sync --locked
# ---------------------------------------------------------------------------


class TestUvRuffGate:
    def _write_pyproject(self, path: Path, content: str) -> None:
        (path / "pyproject.toml").write_text(content, encoding="utf-8")

    def test_hatch_fast_replaces_tool_ruff_only_gate(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        """uv uses the shared fast stack instead of a uv-only ruff branch."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "ruff check ."\n'
            "[tool.ruff]\nline-length = 100\n",
        )

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert ["uv", "run", "check"] in fake_run.calls
        assert not any(cmd[:4] == ["uv", "run", "ruff", "check"] for cmd in fake_run.calls)

    def test_ruff_runs_when_ruff_in_dev_deps(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        """ruff check must run when ruff appears in uv dev-dependencies."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n',
        )

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert any(cmd[:4] == ["uv", "run", "ruff", "check"] for cmd in fake_run.calls)

    def test_ruff_skipped_when_not_configured(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        """ruff check must NOT run when ruff is absent from pyproject.toml."""
        self._write_pyproject(tmp_path, '[project]\nname = "myapp"\n')

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert not any(cmd[:4] == ["uv", "run", "ruff", "check"] for cmd in fake_run.calls)

    def test_ruff_failure_returns_error(self, tmp_path: Path, make_workspace) -> None:
        """ruff check failure must surface as an error string."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n',
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd[:4] == ["uv", "run", "ruff", "check"]:
                return CompletedProcess([], 1, stdout="Found 3 errors.\n", stderr="")
            return _ok()

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is not None
        assert "error" in result.lower() or "ruff" in result.lower()

    def test_uv_sync_failure_short_circuits_ruff(self, tmp_path: Path, make_workspace) -> None:
        """ruff check must NOT run when uv sync --locked fails."""
        self._write_pyproject(tmp_path, "[tool.ruff]\n")
        called_ruff = False

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            nonlocal called_ruff
            if cmd[:4] == ["uv", "run", "ruff", "check"]:
                called_ruff = True
            if cmd == ["uv", "sync", "--locked"]:
                return _fail(stderr="lock file mismatch")
            return _ok()

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws)

        assert not called_ruff


class TestPythonManagerFastGates:
    @pytest.mark.parametrize(
        ("manager", "lock_cmd", "fast_cmd"),
        [
            ("uv", ["uv", "sync", "--locked"], ["uv", "run", "check"]),
            (
                "poetry",
                ["poetry", "check"],
                ["poetry", "run", "check"],
            ),
            ("pdm", ["pdm", "sync", "--clean"], ["pdm", "run", "check"]),
            ("pipenv", ["pipenv", "verify"], ["pipenv", "run", "check"]),
        ],
    )
    def test_lock_gate_then_hatch_fast(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        fast_cmd: list[str],
        make_workspace,
        fake_run: Any,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is None
        # The gate probe may sit between them; the ordering that matters is
        # that the lockfile gate precedes the fallback ladder.
        assert [c for c in fake_run.calls if c[0] != "gate"] == [lock_cmd, fast_cmd]

    @pytest.mark.parametrize(
        ("manager", "lock_cmd", "fast_cmd"),
        [
            ("uv", ["uv", "sync", "--locked"], ["uv", "run", "check"]),
            (
                "poetry",
                ["poetry", "check"],
                ["poetry", "run", "check"],
            ),
            ("pdm", ["pdm", "sync", "--clean"], ["pdm", "run", "check"]),
            ("pipenv", ["pipenv", "verify"], ["pipenv", "run", "check"]),
        ],
    )
    def test_lock_gate_failure_blocks_hatch_fast(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        fast_cmd: list[str],
        make_workspace,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _fail(stderr="lock gate failed") if cmd == lock_cmd else _ok()

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

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
        make_workspace,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )
        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value=None),
            patch("repo_ops_upgrade.verify.run") as run_mock,
        ):
            result = verify_workspace(ws)

        assert result == f"{binary} not on PATH"
        run_mock.assert_not_called()

    @pytest.mark.parametrize(
        ("manager", "lock_cmd", "fast_cmd"),
        [
            ("uv", ["uv", "sync", "--locked"], ["uv", "run", "check"]),
            (
                "poetry",
                ["poetry", "check"],
                ["poetry", "run", "check"],
            ),
            ("pdm", ["pdm", "sync", "--clean"], ["pdm", "run", "check"]),
            ("pipenv", ["pipenv", "verify"], ["pipenv", "run", "check"]),
        ],
    )
    def test_hatch_fast_failure_blocks(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        fast_cmd: list[str],
        make_workspace,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd == fast_cmd:
                return _fail(stderr="fast gate failed")
            return _ok()

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "fast gate failed" in result


# ---------------------------------------------------------------------------
# Node: verify script waterfall
# ---------------------------------------------------------------------------


class TestNodeVerifyScriptWaterfall:
    def _write_pkg(self, path: Path, scripts: dict[str, str] | None = None) -> None:
        import json

        content = {"scripts": scripts or {"ci": "echo ci", "build": "echo build"}}
        (path / "package.json").write_text(json.dumps(content), encoding="utf-8")

    def test_uses_waterfall(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace, fake_run: Any
    ) -> None:
        """The first matching waterfall candidate is used."""
        self._write_pkg(tmp_path, {"build": "echo build"})

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_node(ws)

        assert result is None
        # 'build' is in the waterfall; ci/ci:pushgate are not defined → build used
        assert any("build" in c for c in fake_run.calls)

    def test_waterfall_first_match_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_workspace, fake_run: Any
    ) -> None:
        """Waterfall picks 'ci' over 'build' when both exist."""
        self._write_pkg(tmp_path, {"ci": "echo ci", "build": "echo build", "lint": "echo lint"})

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_node(ws)

        # Only 'ci' should have been run as the script step, not 'build' or 'lint'
        script_step_calls = [
            c for c in fake_run.calls if len(c) > 1 and c[-1] in ("ci", "build", "lint")
        ]
        assert script_step_calls[0][-1] == "ci"
        assert len([c for c in script_step_calls if c[-1] in ("build", "lint")]) == 0


# ---------------------------------------------------------------------------
# Hatch: _verify_hatch_fast extended heuristics
# ---------------------------------------------------------------------------


_HATCH_FAST_CASES = [
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ncheck = "mypy src"\n',
        ["uv", "run", "check"],
        None,
        id="check_script",
    ),
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ntypecheck = "pyright"\n',
        ["uv", "run", "typecheck"],
        None,
        id="typecheck_script",
    ),
    pytest.param(
        '[tool.uv]\ndev-dependencies = ["mypy>=1.0"]\n',
        ["uv", "run", "mypy", "."],
        None,
        id="mypy_dev_deps",
    ),
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ntest = "pytest"\n'
        '[tool.uv]\ndev-dependencies = ["mypy>=1.0"]\n',
        ["uv", "run", "test"],
        ["uv", "run", "mypy", "."],
        id="test_script_over_mypy",
    ),
    pytest.param(
        '[tool.hatch.envs.default]\ndependencies = ["mypy"]\n',
        ["uv", "run", "mypy", "."],
        None,
        id="hatch_env_deps_mypy",
    ),
    pytest.param(
        '[tool.uv]\ndev-dependencies = ["pyrefly>=0.1"]\n'
        '[tool.pyrefly]\nproject-excludes = ["tests/**", "scripts/**"]\n',
        [
            "uv",
            "run",
            "pyrefly",
            "check",
            ".",
            "--project-excludes",
            "tests/**",
            "scripts/**",
        ],
        None,
        id="pyrefly_dev_deps_with_excludes",
    ),
    pytest.param(
        '[tool.hatch.envs.default]\ndependencies = ["pyrefly"]\n',
        ["uv", "run", "pyrefly", "check", "."],
        None,
        id="hatch_env_deps_pyrefly",
    ),
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ntest = "pytest"\n'
        '[tool.uv]\ndev-dependencies = ["pyrefly>=0.1"]\n',
        ["uv", "run", "test"],
        ["uv", "run", "pyrefly", "check", "."],
        id="test_script_over_pyrefly",
    ),
    pytest.param(
        '[tool.uv]\ndev-dependencies = ["mypy>=1.0", "pyrefly>=0.1"]\n',
        ["uv", "run", "mypy", "."],
        ["uv", "run", "pyrefly", "check", "."],
        id="mypy_over_pyrefly",
    ),
    pytest.param(
        '[tool.uv]\ndev-dependencies = ["pytest>=8"]\n',
        ["uv", "run", "pytest", "-q"],
        None,
        id="pytest_dev_deps",
    ),
]


class TestVerifyHatchFast:
    def _write_pyproject(self, path: Path, content: str) -> None:
        (path / "pyproject.toml").write_text(content, encoding="utf-8")

    def _hatch_ws(self, tmp_path: Path, make_workspace) -> Workspace:
        return make_workspace(tmp_path, manager="hatch", ecosystem="python", label="demo")

    @pytest.mark.parametrize(("pyproject_content", "expected_cmd", "absent_cmd"), _HATCH_FAST_CASES)
    def test_hatch_fast_command_selection(
        self,
        tmp_path: Path,
        make_workspace,
        pyproject_content: str,
        expected_cmd: list[str],
        absent_cmd: list[str] | None,
        fake_run: Any,
    ) -> None:
        self._write_pyproject(tmp_path, pyproject_content)

        ws = self._hatch_ws(tmp_path, make_workspace)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert expected_cmd in fake_run.calls
        if absent_cmd is not None:
            assert absent_cmd not in fake_run.calls

    def test_check_script_failure_returns_error(self, tmp_path: Path, make_workspace) -> None:
        """check script failure must surface an error string."""
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.scripts]\ncheck = "mypy src"\n',
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd == ["uv", "run", "check"]:
                return _fail(stderr="type error found")
            return _ok()

        ws = self._hatch_ws(tmp_path, make_workspace)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is not None
        assert "check" in result or "type" in result

    def test_pytest_failure_returns_error(self, tmp_path: Path, make_workspace) -> None:
        """A failing pytest run fails verify, the same way go test does."""
        self._write_pyproject(
            tmp_path,
            '[tool.uv]\ndev-dependencies = ["pytest>=8"]\n',
        )
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            if cmd == ["uv", "run", "pytest", "-q"]:
                return _fail(stderr="1 failed")
            return _ok()

        ws = self._hatch_ws(tmp_path, make_workspace)
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert ["uv", "run", "pytest", "-q"] in calls
        assert not any("--co" in part for cmd in calls for part in cmd)
        assert result is not None
        assert "1 failed" in result


# ---------------------------------------------------------------------------
# gate CLI delegation
# ---------------------------------------------------------------------------


def _gate_json(*names: str, display: str = "cmd") -> str:
    """``gate --json`` output declaring *names*."""
    import json

    return json.dumps({"root": "/demo", "gates": [{"name": n, "display": display} for n in names]})


_GATE_LIST = _gate_json("build", "typecheck", "lint", "workflows", "test")


def _gate_aware_run(calls: list[list[str]], listing: str = _GATE_LIST):
    """A ``run`` double that answers ``gate --json`` with *listing*."""

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
    ) -> CompletedProcess[str]:
        calls.append(cmd)
        return _ok(stdout=listing if cmd[:2] == ["gate", "--json"] else "")

    return fake_run


class TestGateDelegation:
    def _write_pkg(self, path: Path) -> None:
        import json

        (path / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build", "test": "bun test"}}),
            encoding="utf-8",
        )

    def test_declared_gates_replace_the_node_waterfall(
        self, tmp_path: Path, make_workspace
    ) -> None:
        """Every gate the project declares must run, not the first script the waterfall finds."""
        self._write_pkg(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls)),
        ):
            result = verify_node(ws)

        assert result is None
        assert calls[-1] == [
            "gate",
            "--timeout",
            "0",
            "run",
            "build",
            "typecheck",
            "lint",
            "workflows",
            "test",
        ]
        assert ["bun", "run", "build"] not in calls

    def test_failure_names_the_failed_step_not_the_install_progress_line(
        self, tmp_path: Path, make_workspace
    ) -> None:
        """gate's stderr opens with an install progress line; the FAIL line must lead."""
        self._write_pkg(tmp_path)
        out = (
            "gate: ok  build  1s  /var/tmp/gate/build.log\n"
            "gate: FAIL exit 1  bun run soup  2s\n"
            "--- last 1 line(s) ---\nstale SOUP row\n"
            "gate: full log  /var/tmp/gate/soup.log\n"
        )

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd[:2] == ["gate", "--json"]:
                return _ok(stdout=_GATE_LIST)
            if cmd[0] == "gate":
                return _fail(
                    stdout=out, stderr="gate: install: bun install --frozen-lockfile (in /x)"
                )
            return _ok()

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_node(ws)

        assert result is not None
        assert result.startswith("gate: FAIL exit 1  bun run soup")
        assert "gate: install:" not in result
        assert "/var/tmp/gate/soup.log" in result

    def test_gate_failure_is_the_verify_error(self, tmp_path: Path, make_workspace) -> None:
        """A failing gate run must surface gate's own output as the verify failure."""
        self._write_pkg(tmp_path)

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd[:2] == ["gate", "--json"]:
                return _ok(stdout=_GATE_LIST)
            if cmd[0] == "gate":
                return _fail(stderr="typecheck failed: 3 errors")
            return _ok()

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_node(ws)

        assert result == "typecheck failed: 3 errors"

    def test_waterfall_still_runs_when_gate_is_absent(self, tmp_path: Path, make_workspace) -> None:
        """Without the gate CLI, verify must degrade to the built-in waterfall, not fail."""
        self._write_pkg(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value=None),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls)),
        ):
            result = verify_node(ws)

        assert result is None
        assert ["bun", "run", "build"] in calls
        assert not [c for c in calls if c[0] == "gate"]

    def test_multiline_gate_script_adds_no_phantom_gate(
        self, tmp_path: Path, make_workspace
    ) -> None:
        """An indented line inside an inline gate script is not a gate name."""
        self._write_pkg(tmp_path)
        calls: list[list[str]] = []
        listing = _gate_json("build", "sqlfluff", display="full=x\n  git ls-files -- sql")

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls, listing)),
        ):
            result = verify_node(ws)

        assert result is None
        assert calls[-1] == ["gate", "--timeout", "0", "run", "build", "sqlfluff"]


_PYTHON_GATE_LIST = _gate_json("typecheck", "lint", "workflows", "test", "vuln")


class TestPythonGateDelegation:
    """The Python managers delegate to declared gates on the same terms as Node/Go/Rust."""

    def _write_pyproject(self, path: Path) -> None:
        (path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n',
            encoding="utf-8",
        )

    def test_declared_gates_replace_the_hatch_ladder(self, tmp_path: Path, make_workspace) -> None:
        """Every declared gate must run, not the one command the ladder infers."""
        self._write_pyproject(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch(
                "repo_ops_upgrade.verify.run",
                side_effect=_gate_aware_run(calls, _PYTHON_GATE_LIST),
            ),
        ):
            result = verify_workspace(ws)

        assert result is None
        # The lockfile gate is not something gate checks, so it still leads.
        assert calls[0] == ["uv", "sync", "--locked"]
        assert calls[-1] == [
            "gate",
            "--timeout",
            "0",
            "run",
            "typecheck",
            "lint",
            "workflows",
            "test",
            "vuln",
        ]
        assert ["uv", "run", "check"] not in calls

    def test_gate_failure_is_the_verify_error(self, tmp_path: Path, make_workspace) -> None:
        """A failing gate run must surface gate's own output as the verify failure."""
        self._write_pyproject(tmp_path)

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            if cmd[:2] == ["gate", "--json"]:
                return _ok(stdout=_PYTHON_GATE_LIST)
            if cmd[0] == "gate":
                return _fail(stderr="lint failed: 2 errors")
            return _ok()

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/tool"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result == "lint failed: 2 errors"

    def test_ladder_still_runs_when_gate_is_absent(self, tmp_path: Path, make_workspace) -> None:
        """Without the gate CLI, verify must degrade to the built-in ladder, not fail."""
        self._write_pyproject(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with (
            patch(
                "repo_ops_upgrade.verify.shutil.which",
                side_effect=lambda name: None if name == "gate" else "/usr/bin/tool",
            ),
            patch(
                "repo_ops_upgrade.verify.run",
                side_effect=_gate_aware_run(calls, _PYTHON_GATE_LIST),
            ),
        ):
            result = verify_workspace(ws)

        assert result is None
        assert ["uv", "run", "check"] in calls
        assert not [c for c in calls if c[0] == "gate"]
