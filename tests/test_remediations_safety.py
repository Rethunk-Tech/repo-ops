"""Safety-behaviour tests for Go/Rust/core remediation hooks.

Covers the four verified defects:
1. go_generate is default-off behind REPO_OPS_REMEDIATE_GO_GENERATE=1.
2. gofmt/cargo fmt are scoped to files changed by the bump, not the whole
   workspace.
3. Missing tools (shutil.which → None / FileNotFoundError) yield SKIP, not
   fail, so an otherwise-successful dep bump is not marked failed.
4. _has_go_generate prunes vendor/.git/node_modules/testdata directories.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade_remediations_core import remediations as core
from repo_ops_upgrade_remediations_deno import remediations as deno_rem
from repo_ops_upgrade_remediations_go import remediations as go_rem
from repo_ops_upgrade_remediations_py import remediations as py_rem
from repo_ops_upgrade_remediations_rust import remediations as rust_rem

# ── helpers ──────────────────────────────────────────────────────────────────


def _go_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="test",
    )


def _rust_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="rust",
        manager="cargo",
        git_repo=tmp_path,
        label="test",
    )


def _deno_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="deno",
        manager="deno",
        git_repo=tmp_path,
        label="test",
    )


def _py_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="uv",
        git_repo=tmp_path,
        label="test",
    )


def _py_result(tmp_path: Path) -> WorkspaceResult:
    return WorkspaceResult(workspace=_py_ws(tmp_path))


def _go_result(tmp_path: Path) -> WorkspaceResult:
    return WorkspaceResult(workspace=_go_ws(tmp_path))


def _rust_result(tmp_path: Path) -> WorkspaceResult:
    return WorkspaceResult(workspace=_rust_ws(tmp_path))


def _fake_change(name: str) -> Any:
    obj = MagicMock()
    obj.name = name
    return obj


def _make_proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _git_init(path: Path) -> None:
    """Initialise a real bare-minimum git repo in *path*."""
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


def _git_commit_all(path: Path, message: str = "initial") -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Defect 1 — go_generate env gate
# ══════════════════════════════════════════════════════════════════════════════


class TestGoGenerateEnvGate:
    """go_generate must be default-off; only run when REPO_OPS_REMEDIATE_GO_GENERATE=1."""

    def _setup_ws(self, tmp_path: Path) -> tuple[Workspace, WorkspaceResult]:
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "gen.go").write_text(
            "package main\n//go:generate stringer -type=Foo\n"
        )
        ws = _go_ws(tmp_path)
        result = _go_result(tmp_path)
        result.changes = [_fake_change("golang.org/x/tools")]
        return ws, result

    def test_go_generate_not_called_when_gate_off(self, tmp_path: Path) -> None:
        """go generate subprocess is NOT invoked when env gate is absent (default)."""
        _, result = self._setup_ws(tmp_path)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        env_without_gate = {
            k: v for k, v in os.environ.items() if k != "REPO_OPS_REMEDIATE_GO_GENERATE"
        }
        with (
            patch.dict(os.environ, env_without_gate, clear=True),
            patch.object(go_rem, "REMEDIATIONS", [fake_hook]),
            patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run,
        ):
            go_rem.run_remediations(result, apply=True, skip=set())

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] not in called_cmds, (
            "go generate must not run when REPO_OPS_REMEDIATE_GO_GENERATE is unset"
        )

    def test_go_generate_called_when_gate_set(self, tmp_path: Path) -> None:
        """go generate IS invoked when REPO_OPS_REMEDIATE_GO_GENERATE=1."""
        _, result = self._setup_ws(tmp_path)
        ok_proc = _make_proc(0)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        with (
            patch.dict(os.environ, {"REPO_OPS_REMEDIATE_GO_GENERATE": "1"}),
            patch.object(go_rem, "REMEDIATIONS", [fake_hook]),
            patch(
                "repo_ops_upgrade_remediations_go.remediations.run",
                return_value=ok_proc,
            ) as mock_run,
        ):
            go_rem.run_remediations(result, apply=True, skip=set())

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] in called_cmds, (
            "go generate must run when REPO_OPS_REMEDIATE_GO_GENERATE=1"
        )

    def test_go_generate_result_status_stays_ok_when_gate_off(
        self, tmp_path: Path
    ) -> None:
        """Skipping go_generate via the env gate must not affect result.status."""
        _, result = self._setup_ws(tmp_path)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        env_without_gate = {
            k: v for k, v in os.environ.items() if k != "REPO_OPS_REMEDIATE_GO_GENERATE"
        }
        with (
            patch.dict(os.environ, env_without_gate, clear=True),
            patch.object(go_rem, "REMEDIATIONS", [fake_hook]),
        ):
            go_rem.run_remediations(result, apply=True, skip=set())

        assert result.status == "ok"


class TestGoGenerateDispatchPathGate:
    """Production path: orchestration remediations proxy / core.dispatch must gate go_generate."""

    def _setup_ws(self, tmp_path: Path) -> WorkspaceResult:
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "gen.go").write_text(
            "package main\n//go:generate stringer -type=Foo\n"
        )
        result = _go_result(tmp_path)
        result.changes = [_fake_change("golang.org/x/tools")]
        return result

    def test_core_dispatch_skips_go_generate_when_gate_off(
        self, tmp_path: Path
    ) -> None:
        result = self._setup_ws(tmp_path)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        env_without_gate = {
            k: v for k, v in os.environ.items() if k != "REPO_OPS_REMEDIATE_GO_GENERATE"
        }
        with (
            patch.dict(os.environ, env_without_gate, clear=True),
            patch(
                "repo_ops_upgrade_remediations_go.remediations.run"
            ) as mock_run,
            patch.object(go_rem.shutil, "which", return_value="/usr/bin/go"),
        ):
            core.dispatch(result, apply=True, skip=set(), _registry=[fake_hook])

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] not in called_cmds

    def test_core_dispatch_runs_go_generate_when_gate_set(
        self, tmp_path: Path
    ) -> None:
        result = self._setup_ws(tmp_path)
        ok_proc = _make_proc(0)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        with (
            patch.dict(os.environ, {"REPO_OPS_REMEDIATE_GO_GENERATE": "1"}),
            patch(
                "repo_ops_upgrade_remediations_go.remediations.run",
                return_value=ok_proc,
            ) as mock_run,
            patch.object(go_rem.shutil, "which", return_value="/usr/bin/go"),
        ):
            core.dispatch(result, apply=True, skip=set(), _registry=[fake_hook])

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] in called_cmds

    def test_orchestration_proxy_skips_go_generate_when_gate_off(
        self, tmp_path: Path
    ) -> None:
        """orchestration.remediations.run_remediations routes through dispatch gate."""
        from repo_ops_upgrade.orchestration import remediations as orch_rem

        result = self._setup_ws(tmp_path)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        env_without_gate = {
            k: v for k, v in os.environ.items() if k != "REPO_OPS_REMEDIATE_GO_GENERATE"
        }
        with (
            patch.dict(os.environ, env_without_gate, clear=True),
            patch(
                "repo_ops_upgrade_remediations_go.remediations.run"
            ) as mock_run,
            patch.object(go_rem.shutil, "which", return_value="/usr/bin/go"),
            patch(
                "repo_ops_upgrade.orchestration._dispatch_remediations",
                side_effect=lambda result, *, apply, skip: core.dispatch(
                    result, apply=apply, skip=skip, _registry=[fake_hook]
                ),
            ),
        ):
            orch_rem.run_remediations(result, apply=True, skip=set())

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] not in called_cmds, (
            "orchestration proxy must not run go generate when env gate is unset"
        )

    def test_orchestration_proxy_runs_go_generate_when_gate_set(
        self, tmp_path: Path
    ) -> None:
        """orchestration.remediations proxy respects REPO_OPS_REMEDIATE_GO_GENERATE=1."""
        from repo_ops_upgrade.orchestration import remediations as orch_rem

        result = self._setup_ws(tmp_path)
        ok_proc = _make_proc(0)
        fake_hook = go_rem.RemediationHook(
            "go_generate",
            go_rem.detect_go_generate,
            go_rem.report_go_generate,
            go_rem.apply_go_generate,
        )
        with (
            patch.dict(os.environ, {"REPO_OPS_REMEDIATE_GO_GENERATE": "1"}),
            patch(
                "repo_ops_upgrade_remediations_go.remediations.run",
                return_value=ok_proc,
            ) as mock_run,
            patch.object(go_rem.shutil, "which", return_value="/usr/bin/go"),
            patch(
                "repo_ops_upgrade.orchestration._dispatch_remediations",
                side_effect=lambda result, *, apply, skip: core.dispatch(
                    result, apply=apply, skip=skip, _registry=[fake_hook]
                ),
            ),
        ):
            orch_rem.run_remediations(result, apply=True, skip=set())

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] in called_cmds


# ══════════════════════════════════════════════════════════════════════════════
# Defect 2 — formatting scoped to changed files
# ══════════════════════════════════════════════════════════════════════════════


class TestGofmtScopedToChangedFiles:
    """gofmt must format only .go files changed by the bump, not unrelated ones."""

    def test_gofmt_formats_only_changed_go_files(self, tmp_path: Path) -> None:
        """gofmt command targets only the files git diff reports as changed."""
        # Set up a real git repo with two .go files.
        _git_init(tmp_path)
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
        (tmp_path / "other.go").write_text("package main\nfunc other() {}\n")
        _git_commit_all(tmp_path)

        # Simulate bump: main.go is modified (e.g., import path adjustment).
        (tmp_path / "main.go").write_text(
            'package main\nimport "fmt"\nfunc main() { fmt.Println("hi") }\n'
        )
        # other.go is untouched.

        ws = _go_ws(tmp_path)
        ctx: dict[str, Any] = {
            "changes": [_fake_change("golang.org/x/net")],
            "apply": True,
        }
        ok_proc = _make_proc(0)

        with patch(
            "repo_ops_upgrade_remediations_go.remediations.run", return_value=ok_proc
        ) as mock_run:
            ok, _ = go_rem.apply_gofmt(ws, ctx)

        assert ok is True
        gofmt_cmd = mock_run.call_args[0][0]
        # Must include main.go (changed).
        assert any("main.go" in arg for arg in gofmt_cmd), (
            f"gofmt should target main.go; got {gofmt_cmd}"
        )
        # Must NOT include other.go (unchanged).
        assert not any("other.go" in arg for arg in gofmt_cmd), (
            f"gofmt must not target other.go; got {gofmt_cmd}"
        )

    def test_gofmt_no_op_when_no_go_files_changed(self, tmp_path: Path) -> None:
        """gofmt returns ok without calling run when only go.mod/go.sum changed."""
        _git_init(tmp_path)
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
        _git_commit_all(tmp_path)

        # Bump: only go.mod changes (version bump).
        (tmp_path / "go.mod").write_text(
            "module example.com/m\n\ngo 1.21\n\nrequire golang.org/x/net v0.20.0\n"
        )

        ws = _go_ws(tmp_path)
        ctx: dict[str, Any] = {
            "changes": [_fake_change("golang.org/x/net")],
            "apply": True,
        }

        with patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run:
            ok, detail = go_rem.apply_gofmt(ws, ctx)

        assert ok is True
        assert "no changed .go source files" in detail
        # gofmt (via run) must NOT have been called.
        assert not any(c[0][0][0] == "gofmt" for c in mock_run.call_args_list), (
            "gofmt must not run when no .go source files changed"
        )


class TestCargoFmtScopedToChangedFiles:
    """cargo fmt must format only .rs files changed by the bump, not unrelated ones."""

    def test_cargo_fmt_formats_only_changed_rs_files(self, tmp_path: Path) -> None:
        """cargo fmt -- targets only the files git diff reports as changed."""
        _git_init(tmp_path)
        (tmp_path / "Cargo.toml").write_text(
            "[package]\nname = 'foo'\nversion = '0.1.0'\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")
        (tmp_path / "src" / "lib.rs").write_text("pub fn helper() {}\n")
        _git_commit_all(tmp_path)

        # Simulate bump: main.rs is modified.
        (tmp_path / "src" / "main.rs").write_text(
            'fn main() {\n    println!("hello");\n}\n'
        )
        # lib.rs is untouched.

        ws = _rust_ws(tmp_path)
        ctx: dict[str, Any] = {
            "changes": [_fake_change("serde")],
            "apply": True,
        }
        ok_proc = _make_proc(0)

        with patch(
            "repo_ops_upgrade_remediations_rust.remediations.run", return_value=ok_proc
        ) as mock_run:
            ok, _ = rust_rem.apply_cargo_fix(ws, ctx)

        assert ok is True
        cargo_cmd = mock_run.call_args[0][0]
        assert any("main.rs" in arg for arg in cargo_cmd), (
            f"cargo fmt should target main.rs; got {cargo_cmd}"
        )
        assert not any("lib.rs" in arg for arg in cargo_cmd), (
            f"cargo fmt must not target lib.rs; got {cargo_cmd}"
        )

    def test_cargo_fmt_no_op_when_no_rs_files_changed(self, tmp_path: Path) -> None:
        """cargo fmt returns ok without running when only Cargo.toml/Cargo.lock changed."""
        _git_init(tmp_path)
        (tmp_path / "Cargo.toml").write_text(
            "[package]\nname = 'foo'\nversion = '0.1.0'\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")
        _git_commit_all(tmp_path)

        # Bump: only Cargo.toml changes.
        (tmp_path / "Cargo.toml").write_text(
            "[package]\nname = 'foo'\nversion = '0.1.0'\n\n[dependencies]\nserde = '1.0'\n"
        )

        ws = _rust_ws(tmp_path)
        ctx: dict[str, Any] = {
            "changes": [_fake_change("serde")],
            "apply": True,
        }

        with patch("repo_ops_upgrade_remediations_rust.remediations.run") as mock_run:
            ok, detail = rust_rem.apply_cargo_fix(ws, ctx)

        assert ok is True
        assert "no changed .rs source files" in detail
        assert not any(c[0][0][0] == "cargo" for c in mock_run.call_args_list), (
            "cargo fmt must not run when no .rs source files changed"
        )


class TestDenoFmtScopedToChangedFiles:
    """deno fmt must format only Deno-formattable files changed by the bump."""

    def test_deno_fmt_formats_only_changed_source_files(self, tmp_path: Path) -> None:
        _git_init(tmp_path)
        (tmp_path / "deno.json").write_text("{}\n")
        (tmp_path / "main.ts").write_text("console.log('a');\n")
        (tmp_path / "other.ts").write_text("console.log('b');\n")
        _git_commit_all(tmp_path)

        (tmp_path / "main.ts").write_text('console.log("changed");\n')

        ws = _deno_ws(tmp_path)
        ctx: dict[str, Any] = {"changes": [_fake_change("std")], "apply": True}
        ok_proc = _make_proc(0)

        with patch(
            "repo_ops_upgrade_remediations_deno.remediations.run", return_value=ok_proc
        ) as mock_run:
            ok, _ = deno_rem.apply_deno_fix(ws, ctx)

        assert ok is True
        deno_cmd = mock_run.call_args[0][0]
        assert any("main.ts" in arg for arg in deno_cmd), (
            f"deno fmt should target main.ts; got {deno_cmd}"
        )
        assert not any("other.ts" in arg for arg in deno_cmd), (
            f"deno fmt must not target other.ts; got {deno_cmd}"
        )

    def test_deno_fmt_no_op_when_only_lock_or_config_changed(
        self, tmp_path: Path
    ) -> None:
        """deno fmt returns ok without running when only deno.json/deno.lock changed."""
        _git_init(tmp_path)
        (tmp_path / "deno.json").write_text("{}\n")
        (tmp_path / "main.ts").write_text("console.log('a');\n")
        _git_commit_all(tmp_path)

        (tmp_path / "deno.json").write_text('{"tasks": {}}\n')
        (tmp_path / "deno.lock").write_text("{}\n")

        ws = _deno_ws(tmp_path)
        ctx: dict[str, Any] = {"changes": [_fake_change("std")], "apply": True}

        with patch("repo_ops_upgrade_remediations_deno.remediations.run") as mock_run:
            ok, detail = deno_rem.apply_deno_fix(ws, ctx)

        assert ok is True
        assert "no changed source files" in detail
        assert not any(
            c[0][0] and c[0][0][0] == "deno" for c in mock_run.call_args_list
        ), "deno fmt must not run when only lock/config changed"


class TestGofmtDetailReflectsArgv:
    """apply_gofmt success detail must reflect the actual argv, not always gofmt -w ."""

    def test_gofmt_success_detail_includes_changed_files(self, tmp_path: Path) -> None:
        _git_init(tmp_path)
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
        _git_commit_all(tmp_path)
        (tmp_path / "main.go").write_text(
            'package main\nimport "fmt"\nfunc main() { fmt.Println("x") }\n'
        )

        ws = _go_ws(tmp_path)
        ctx: dict[str, Any] = {
            "changes": [_fake_change("golang.org/x/net")],
            "apply": True,
        }
        with patch(
            "repo_ops_upgrade_remediations_go.remediations.run",
            return_value=_make_proc(0),
        ):
            ok, detail = go_rem.apply_gofmt(ws, ctx)

        assert ok is True
        assert "main.go" in detail
        assert detail != "gofmt -w . (ok)"


# ══════════════════════════════════════════════════════════════════════════════
# Defect 3 — tool-missing → SKIP, not fail
# ══════════════════════════════════════════════════════════════════════════════


class TestToolMissingYieldsSkip:
    """A missing remediation tool must never fail an otherwise-successful apply."""

    # -- gofmt via shutil.which → None ----------------------------------------

    def test_detect_gofmt_false_when_gofmt_missing(self, tmp_path: Path) -> None:
        """detect_gofmt returns False when gofmt is not installed."""
        (tmp_path / "go.mod").write_text("module example.com/m\n")
        ws = _go_ws(tmp_path)
        ctx: dict[str, Any] = {"changes": [_fake_change("pkg")], "apply": True}

        with patch("repo_ops_upgrade_remediations_go.remediations.shutil") as mock_sh:
            mock_sh.which.return_value = None
            detected = go_rem.detect_gofmt(ws, ctx)

        assert detected is False

    def test_gofmt_missing_workspace_apply_stays_successful(
        self, tmp_path: Path
    ) -> None:
        """result.status stays 'ok' when gofmt is absent (detect skips it)."""
        (tmp_path / "go.mod").write_text("module example.com/m\n")
        result = _go_result(tmp_path)
        result.changes = [_fake_change("golang.org/x/net")]

        fake = go_rem.RemediationHook(
            "gofmt", go_rem.detect_gofmt, go_rem.report_gofmt, go_rem.apply_gofmt
        )
        with (
            patch.object(go_rem, "REMEDIATIONS", [fake]),
            patch("repo_ops_upgrade_remediations_go.remediations.shutil") as mock_sh,
        ):
            mock_sh.which.return_value = None
            go_rem.run_remediations(result, apply=True, skip=set())

        assert result.status == "ok"
        # No remediation entry should appear (hook was silently skipped).
        assert result.remediations == []

    # -- cargo via shutil.which → None ----------------------------------------

    def test_detect_cargo_fix_false_when_cargo_missing(self, tmp_path: Path) -> None:
        """detect_cargo_fix returns False when cargo is not installed."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        ws = _rust_ws(tmp_path)
        ctx: dict[str, Any] = {"changes": [_fake_change("serde")], "apply": True}

        with patch("repo_ops_upgrade_remediations_rust.remediations.shutil") as mock_sh:
            mock_sh.which.return_value = None
            detected = rust_rem.detect_cargo_fix(ws, ctx)

        assert detected is False

    def test_cargo_missing_workspace_apply_stays_successful(
        self, tmp_path: Path
    ) -> None:
        """result.status stays 'ok' when cargo is absent (detect skips it)."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        result = _rust_result(tmp_path)
        result.changes = [_fake_change("serde")]

        fake = rust_rem.RemediationHook(
            "cargo_fix",
            rust_rem.detect_cargo_fix,
            rust_rem.report_cargo_fix,
            rust_rem.apply_cargo_fix,
        )
        with (
            patch.object(rust_rem, "REMEDIATIONS", [fake]),
            patch("repo_ops_upgrade_remediations_rust.remediations.shutil") as mock_sh,
        ):
            mock_sh.which.return_value = None
            rust_rem.run_remediations(result, apply=True, skip=set())

        assert result.status == "ok"
        assert result.remediations == []

    # -- ruff via shutil.which → None ----------------------------------------

    def test_detect_ruff_false_when_ruff_missing(self, tmp_path: Path) -> None:
        """detect_ruff returns False when ruff is not installed."""
        (tmp_path / "ruff.toml").write_text("line-length = 88\n")
        ws = _py_ws(tmp_path)
        ctx: dict[str, Any] = {"changes": [_fake_change("httpx")], "apply": True}

        with patch("repo_ops_upgrade_remediations_py.remediations.shutil") as mock_sh:
            mock_sh.which.return_value = None
            detected = py_rem.detect_ruff(ws, ctx)

        assert detected is False

    def test_ruff_missing_workspace_apply_stays_successful(
        self, tmp_path: Path
    ) -> None:
        """result.status stays 'ok' when ruff is absent (detect skips it)."""
        (tmp_path / "ruff.toml").write_text("line-length = 88\n")
        result = _py_result(tmp_path)
        result.changes = [_fake_change("httpx")]

        fake = py_rem.RemediationHook(
            "ruff_fix", py_rem.detect_ruff, py_rem.report_ruff, py_rem.apply_ruff
        )
        with (
            patch.object(py_rem, "REMEDIATIONS", [fake]),
            patch("repo_ops_upgrade_remediations_py.remediations.shutil") as mock_sh,
        ):
            mock_sh.which.return_value = None
            py_rem.run_remediations(result, apply=True, skip=set())

        assert result.status == "ok"
        assert result.remediations == []

    # -- FileNotFoundError path in run_hooks ----------------------------------

    def test_file_not_found_in_apply_yields_skip_not_fail(self, tmp_path: Path) -> None:
        """FileNotFoundError raised by apply sets status='skip', not 'fail'."""
        ws = Workspace(
            path=tmp_path,
            ecosystem="node",
            manager="npm",
            git_repo=tmp_path,
            label="test",
        )
        result = WorkspaceResult(workspace=ws)

        def _apply_raises(_ws: Any, _ctx: Any) -> Any:
            raise FileNotFoundError("[Errno 2] No such file or directory: 'gofmt'")

        hook = core.RemediationHook(
            "missing_tool",
            lambda _ws, _ctx: True,
            lambda _ws, _ctx: [],
            _apply_raises,
        )
        core.run_hooks([hook], result, apply=True, skip=set())

        assert result.status == "ok", (
            "result.status must not become 'fail' when a tool is missing"
        )
        assert result.remediations[0]["status"] == "skip"
        assert "tool not found" in result.remediations[0]["detail"]

    def test_go_generate_missing_tool_stays_successful(self, tmp_path: Path) -> None:
        """Missing 'go' binary: detect_go_generate returns False, result stays ok."""
        (tmp_path / "go.mod").write_text("module example.com/m\n")
        (tmp_path / "gen.go").write_text(
            "package main\n//go:generate stringer -type=Foo\n"
        )
        ws = _go_ws(tmp_path)
        ctx: dict[str, Any] = {"changes": [_fake_change("pkg")], "apply": True}

        with patch("repo_ops_upgrade_remediations_go.remediations.shutil") as mock_sh:
            mock_sh.which.return_value = None
            detected = go_rem.detect_go_generate(ws, ctx)

        assert detected is False


# ══════════════════════════════════════════════════════════════════════════════
# Defect 4 — _has_go_generate SKIP_DIRS pruning
# ══════════════════════════════════════════════════════════════════════════════


class TestSkipDirsPruning:
    """_has_go_generate must not traverse vendor/.git/node_modules/testdata."""

    @pytest.mark.parametrize(
        "skip_dir",
        ["vendor", ".git", "node_modules", "testdata", "_vendor"],
    )
    def test_has_go_generate_skips_directory(
        self, tmp_path: Path, skip_dir: str
    ) -> None:
        """//go:generate inside a skip-dir is not detected."""
        skip_path = tmp_path / skip_dir
        skip_path.mkdir()
        (skip_path / "generated.go").write_text(
            "package generated\n//go:generate stringer -type=T\n"
        )
        assert go_rem._has_go_generate(tmp_path) is False, (
            f"//go:generate in {skip_dir}/ must not trigger detection"
        )

    def test_has_go_generate_finds_directive_outside_skip_dirs(
        self, tmp_path: Path
    ) -> None:
        """//go:generate in a normal subdirectory IS detected."""
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "skip.go").write_text(
            "package skip\n//go:generate stringer -type=T\n"
        )
        (tmp_path / "internal").mkdir()
        (tmp_path / "internal" / "real.go").write_text(
            "package internal\n//go:generate mockgen -source=real.go\n"
        )
        assert go_rem._has_go_generate(tmp_path) is True, (
            "//go:generate in a non-skipped directory must be detected"
        )

    def test_has_go_generate_false_when_only_skip_dirs_exist(
        self, tmp_path: Path
    ) -> None:
        """When ALL .go files with directives are in skip dirs, returns False."""
        for skip_dir in go_rem._SKIP_DIRS:
            d = tmp_path / skip_dir
            d.mkdir(exist_ok=True)
            (d / "f.go").write_text("package p\n//go:generate something\n")
        assert go_rem._has_go_generate(tmp_path) is False
