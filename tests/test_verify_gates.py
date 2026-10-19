"""Per-ecosystem verify gates.

- Every ecosystem delegates to the gates the project declares, through ``gate``.
- Without ``gate``, or where it detects nothing, verify fails rather than
  guessing a script or command.
- Lockfile steps (immutable install, ``uv sync --locked``, ``go mod verify``)
  run first and short-circuit.
"""

from __future__ import annotations

import json
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

_GATE_RUN_TEST = ["gate", "--timeout", "0", "run", "test"]


def _ok(stdout: str = "", stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess([], 0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "err") -> CompletedProcess[str]:
    return CompletedProcess([], 1, stdout=stdout, stderr=stderr)


def _no_gate(name: str) -> str | None:
    """A ``shutil.which`` double: every binary resolves except ``gate``."""
    return None if name == "gate" else f"/usr/bin/{name}"


def _gate_json(*names: str, display: str = "cmd") -> str:
    """``gate --json`` output declaring *names*."""
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


def _gate_failing_run(stderr: str, listing: str = _GATE_LIST):
    """A ``run`` double whose ``gate run`` fails with *stderr*."""

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
    ) -> CompletedProcess[str]:
        if cmd[:2] == ["gate", "--json"]:
            return _ok(stdout=listing)
        if cmd[0] == "gate":
            return _fail(stderr=stderr)
        return _ok()

    return fake_run


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


class TestGoVerifyGates:
    def test_declared_gates_run_after_mod_verify(
        self, tmp_path: Path, make_workspace, fake_run: Any, gate_on_path: None
    ) -> None:
        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls == [["go", "mod", "verify"], ["gate", "--json"], _GATE_RUN_TEST]

    def test_go_mod_verify_failure_short_circuits(
        self, tmp_path: Path, make_workspace, gate_on_path: None
    ) -> None:
        """gate must NOT run if go mod verify fails."""
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
        assert not any(c[0] == "gate" for c in calls)

    def test_gate_failure_returns_error(
        self, tmp_path: Path, make_workspace, gate_on_path: None
    ) -> None:
        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch(
            "repo_ops_upgrade.verify.run", side_effect=_gate_failing_run("undefined: foo.Bar")
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "undefined" in result

    def test_missing_gate_is_an_error_not_a_go_build(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert fake_run.calls == [["go", "mod", "verify"]]

    def test_gate_detecting_nothing_is_an_error_not_a_go_build(
        self, tmp_path: Path, make_workspace, gate_on_path: None
    ) -> None:
        calls: list[list[str]] = []
        ws = make_workspace(tmp_path, manager="go", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls, _gate_json())):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate detects no gate" in result
        assert not any(c[:2] in (["go", "build"], ["go", "test"]) for c in calls)


class TestRustVerifyGates:
    def test_declared_gates_run(
        self, tmp_path: Path, make_workspace, fake_run: Any, gate_on_path: None
    ) -> None:
        ws = make_workspace(tmp_path, manager="rust", ecosystem="go", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls == [["gate", "--json"], _GATE_RUN_TEST]

    def test_missing_gate_is_an_error_not_a_cargo_run(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        ws = make_workspace(tmp_path, manager="rust", ecosystem="go", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert fake_run.calls == []


# ---------------------------------------------------------------------------
# Python managers: lockfile step, then gate
# ---------------------------------------------------------------------------

_PYPROJECT_CHECK_SCRIPT = '[project]\nname = "demo"\n[project.scripts]\ncheck = "mypy ."\n'


class TestUvLockThenGate:
    def _write_pyproject(self, path: Path, content: str) -> None:
        (path / "pyproject.toml").write_text(content, encoding="utf-8")

    def test_declared_gate_replaces_tool_ruff_only_gate(
        self, tmp_path: Path, make_workspace, fake_run: Any, gate_on_path: None
    ) -> None:
        self._write_pyproject(
            tmp_path,
            '[project]\nname = "demo"\n[project.scripts]\ncheck = "ruff check ."\n'
            "[tool.ruff]\nline-length = 100\n",
        )

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls == [["uv", "sync", "--locked"], ["gate", "--json"], _GATE_RUN_TEST]

    def test_ruff_in_dev_deps_is_not_run_without_gate(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        self._write_pyproject(tmp_path, '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n')

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert fake_run.calls == [["uv", "sync", "--locked"]]

    def test_gate_runs_when_pyproject_declares_nothing(
        self, tmp_path: Path, make_workspace, fake_run: Any, gate_on_path: None
    ) -> None:
        self._write_pyproject(tmp_path, '[project]\nname = "myapp"\n')

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert _GATE_RUN_TEST in fake_run.calls

    def test_gate_failure_returns_error(
        self, tmp_path: Path, make_workspace, gate_on_path: None
    ) -> None:
        self._write_pyproject(tmp_path, '[tool.uv]\ndev-dependencies = ["ruff>=0.4"]\n')

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch(
            "repo_ops_upgrade.verify.run",
            side_effect=_gate_failing_run("Found 3 errors.", _PYTHON_GATE_LIST),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "Found 3 errors." in result

    def test_uv_sync_failure_short_circuits_gate(
        self, tmp_path: Path, make_workspace, gate_on_path: None
    ) -> None:
        self._write_pyproject(tmp_path, "[tool.ruff]\n")
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return (
                _fail(stderr="lock file mismatch") if cmd == ["uv", "sync", "--locked"] else _ok()
            )

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            verify_workspace(ws)

        assert calls == [["uv", "sync", "--locked"]]


_PYTHON_LOCK_CASES = [
    ("uv", ["uv", "sync", "--locked"]),
    ("poetry", ["poetry", "check"]),
    ("pdm", ["pdm", "sync", "--clean"]),
    ("pipenv", ["pipenv", "verify"]),
]


class TestPythonManagerGates:
    @pytest.mark.parametrize(("manager", "lock_cmd"), _PYTHON_LOCK_CASES)
    def test_lock_gate_then_declared_gates(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        make_workspace,
        fake_run: Any,
        gate_on_path: None,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls == [lock_cmd, ["gate", "--json"], _GATE_RUN_TEST]

    @pytest.mark.parametrize(("manager", "lock_cmd"), _PYTHON_LOCK_CASES)
    def test_lock_gate_failure_blocks_gate(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        make_workspace,
        gate_on_path: None,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")
        calls: list[list[str]] = []

        def fake_run(
            cmd: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None
        ) -> CompletedProcess[str]:
            calls.append(cmd)
            return _fail(stderr="lock gate failed") if cmd == lock_cmd else _ok()

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is not None
        assert "failed" in result
        assert calls == [lock_cmd]

    @pytest.mark.parametrize(
        ("manager", "binary"),
        [("uv", "uv"), ("poetry", "poetry"), ("pdm", "pdm"), ("pipenv", "pipenv")],
    )
    def test_missing_manager_binary_fails_closed(
        self,
        tmp_path: Path,
        manager: str,
        binary: str,
        make_workspace,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")
        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value=None),
            patch("repo_ops_upgrade.verify.run") as run_mock,
        ):
            result = verify_workspace(ws)

        assert result == f"{binary} not on PATH"
        run_mock.assert_not_called()

    @pytest.mark.parametrize(("manager", "lock_cmd"), _PYTHON_LOCK_CASES)
    def test_gate_failure_blocks(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        make_workspace,
        gate_on_path: None,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with patch(
            "repo_ops_upgrade.verify.run", side_effect=_gate_failing_run("fast gate failed")
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "fast gate failed" in result

    @pytest.mark.parametrize(("manager", "lock_cmd"), _PYTHON_LOCK_CASES)
    def test_missing_gate_is_an_error_after_the_lock_gate(
        self,
        tmp_path: Path,
        manager: str,
        lock_cmd: list[str],
        make_workspace,
        fake_run: Any,
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")

        ws = make_workspace(tmp_path, manager=manager, ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert fake_run.calls == [lock_cmd]


# ---------------------------------------------------------------------------
# Node: no gate, no guess
# ---------------------------------------------------------------------------


class TestNodeVerifyWithoutGate:
    def _write_pkg(self, path: Path, scripts: dict[str, str]) -> None:
        (path / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")

    @pytest.mark.parametrize(
        "scripts",
        [
            pytest.param({"build": "echo build"}, id="build_only"),
            pytest.param({"ci": "echo ci", "build": "echo build", "lint": "echo lint"}, id="ci"),
            pytest.param({"ci:pushgate": "echo pushgate"}, id="ci_pushgate"),
            pytest.param({"lint": "echo lint"}, id="lint_only"),
        ],
    )
    def test_package_scripts_are_not_run_when_gate_is_absent(
        self, tmp_path: Path, make_workspace, fake_run: Any, scripts: dict[str, str]
    ) -> None:
        self._write_pkg(tmp_path, scripts)

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_node(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert fake_run.calls == [["npm", "ci", "--ignore-scripts"]]

    def test_biome_lint_is_not_run_when_gate_is_absent(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        self._write_pkg(tmp_path, {"lint": "biome check ."})
        (tmp_path / "biome.json").write_text("{}", encoding="utf-8")

        ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_node(ws)

        assert result is not None
        assert fake_run.calls == [["npm", "ci", "--ignore-scripts"]]


# ---------------------------------------------------------------------------
# Python (hatch): nothing the pyproject declares is run without gate
# ---------------------------------------------------------------------------

_HATCH_PYPROJECTS = [
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ncheck = "mypy src"\n', id="check_script"
    ),
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ntypecheck = "pyright"\n', id="typecheck_script"
    ),
    pytest.param('[tool.uv]\ndev-dependencies = ["mypy>=1.0"]\n', id="mypy_dev_deps"),
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ntest = "pytest"\n'
        '[tool.uv]\ndev-dependencies = ["mypy>=1.0"]\n',
        id="test_script_over_mypy",
    ),
    pytest.param('[tool.hatch.envs.default]\ndependencies = ["mypy"]\n', id="hatch_env_deps_mypy"),
    pytest.param(
        '[tool.uv]\ndev-dependencies = ["pyrefly>=0.1"]\n'
        '[tool.pyrefly]\nproject-excludes = ["tests/**", "scripts/**"]\n',
        id="pyrefly_dev_deps_with_excludes",
    ),
    pytest.param(
        '[tool.hatch.envs.default]\ndependencies = ["pyrefly"]\n', id="hatch_env_deps_pyrefly"
    ),
    pytest.param(
        '[project]\nname = "x"\n[project.scripts]\ntest = "pytest"\n'
        '[tool.uv]\ndev-dependencies = ["pyrefly>=0.1"]\n',
        id="test_script_over_pyrefly",
    ),
    pytest.param(
        '[tool.uv]\ndev-dependencies = ["mypy>=1.0", "pyrefly>=0.1"]\n', id="mypy_over_pyrefly"
    ),
    pytest.param('[tool.uv]\ndev-dependencies = ["pytest>=8"]\n', id="pytest_dev_deps"),
]


class TestHatchVerify:
    def _hatch_ws(self, tmp_path: Path, make_workspace) -> Workspace:
        return make_workspace(tmp_path, manager="hatch", ecosystem="python", label="demo")

    @pytest.mark.parametrize("pyproject_content", _HATCH_PYPROJECTS)
    def test_no_gate_is_an_error_whatever_the_pyproject_declares(
        self, tmp_path: Path, make_workspace, pyproject_content: str, fake_run: Any
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

        ws = self._hatch_ws(tmp_path, make_workspace)
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert fake_run.calls == []

    def test_gate_detecting_nothing_is_an_error(self, tmp_path: Path, make_workspace) -> None:
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")
        calls: list[list[str]] = []

        ws = self._hatch_ws(tmp_path, make_workspace)
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls, _gate_json())),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate detects no gate" in result
        assert calls == [["gate", "--json"]]

    def test_check_script_is_not_the_verdict(self, tmp_path: Path, make_workspace) -> None:
        """A failing gate surfaces its own output, not a guessed ``check`` script's."""
        (tmp_path / "pyproject.toml").write_text(_PYPROJECT_CHECK_SCRIPT, encoding="utf-8")

        ws = self._hatch_ws(tmp_path, make_workspace)
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_failing_run("type error found")),
        ):
            result = verify_workspace(ws)

        assert result == "type error found"

    def test_pytest_failure_comes_from_gate(self, tmp_path: Path, make_workspace) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.uv]\ndev-dependencies = ["pytest>=8"]\n', encoding="utf-8"
        )

        ws = self._hatch_ws(tmp_path, make_workspace)
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_failing_run("1 failed")),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "1 failed" in result


# ---------------------------------------------------------------------------
# gate CLI delegation
# ---------------------------------------------------------------------------


class TestGateDelegation:
    def _write_pkg(self, path: Path) -> None:
        (path / "package.json").write_text(
            json.dumps({"scripts": {"build": "vite build", "test": "bun test"}}),
            encoding="utf-8",
        )

    def test_declared_gates_run_not_a_package_script(self, tmp_path: Path, make_workspace) -> None:
        """Every gate the project declares must run, not a script guessed from package.json."""
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

    def test_absent_gate_is_a_verify_error(self, tmp_path: Path, make_workspace) -> None:
        """Without the gate CLI, verify fails after the install step instead of guessing a script."""
        self._write_pkg(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value=None),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls)),
        ):
            result = verify_node(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert calls == [["bun", "install", "--frozen-lockfile", "--ignore-scripts"]]

    def test_gate_detecting_nothing_is_a_verify_error(self, tmp_path: Path, make_workspace) -> None:
        self._write_pkg(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="bun", ecosystem="node", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/gate"),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls, _gate_json())),
        ):
            result = verify_node(ws)

        assert result is not None
        assert "gate detects no gate" in result
        assert ["bun", "run", "build"] not in calls

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

    def test_declared_gates_run_not_an_inferred_command(
        self, tmp_path: Path, make_workspace
    ) -> None:
        """Every declared gate must run, not one command inferred from the pyproject."""
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

    def test_absent_gate_is_a_verify_error(self, tmp_path: Path, make_workspace) -> None:
        """Without the gate CLI, verify fails after the lock step instead of guessing a command."""
        self._write_pyproject(tmp_path)
        calls: list[list[str]] = []

        ws = make_workspace(tmp_path, manager="uv", ecosystem="python", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch(
                "repo_ops_upgrade.verify.run",
                side_effect=_gate_aware_run(calls, _PYTHON_GATE_LIST),
            ),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert calls == [["uv", "sync", "--locked"]]


class TestDenoVerify:
    def test_frozen_install_then_gate(
        self, tmp_path: Path, make_workspace, fake_run: Any, gate_on_path: None
    ) -> None:
        (tmp_path / "deno.lock").write_text("{}\n", encoding="utf-8")
        ws = make_workspace(tmp_path, manager="deno", ecosystem="deno", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls[0] == ["deno", "install", "--frozen"]
        assert fake_run.calls[-1] == _GATE_RUN_TEST

    def test_check_then_gate_without_lock(
        self, tmp_path: Path, make_workspace, fake_run: Any, gate_on_path: None
    ) -> None:
        ws = make_workspace(tmp_path, manager="deno", ecosystem="deno", label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            result = verify_workspace(ws)

        assert result is None
        assert fake_run.calls[0] == ["deno", "check", "."]
        assert fake_run.calls[-1] == _GATE_RUN_TEST

    def test_frozen_install_without_gate_is_an_error(self, tmp_path: Path, make_workspace) -> None:
        (tmp_path / "deno.lock").write_text("{}\n", encoding="utf-8")
        calls: list[list[str]] = []
        ws = make_workspace(tmp_path, manager="deno", ecosystem="deno", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", side_effect=_no_gate),
            patch("repo_ops_upgrade.verify.run", side_effect=_gate_aware_run(calls)),
        ):
            result = verify_workspace(ws)

        assert result is not None
        assert "gate not on PATH" in result
        assert calls == [["deno", "install", "--frozen"]]
