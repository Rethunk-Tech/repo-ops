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
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from repo_ops_models import WorkspaceResult
from repo_ops_upgrade_remediations_core import remediations as core
from repo_ops_upgrade_remediations_deno import remediations as deno_rem
from repo_ops_upgrade_remediations_go import remediations as go_rem
from repo_ops_upgrade_remediations_py import remediations as py_rem
from repo_ops_upgrade_remediations_rust import remediations as rust_rem

# ── helpers ──────────────────────────────────────────────────────────────────


def _go_result(tmp_path: Path, make_workspace: Any) -> WorkspaceResult:
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
    return WorkspaceResult(workspace=ws)


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

    def _setup_ws(
        self, tmp_path: Path, make_workspace: Any, make_change: Any
    ) -> WorkspaceResult:
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "gen.go").write_text(
            "package main\n//go:generate stringer -type=Foo\n"
        )
        result = _go_result(tmp_path, make_workspace)
        result.changes = [make_change("golang.org/x/tools")]
        return result

    def test_go_generate_not_called_and_status_stays_ok_when_gate_off(
        self, tmp_path: Path, make_workspace: Any, make_change: Any
    ) -> None:
        """Skipping go_generate via the absent env gate must not run it or fail the result."""
        result = self._setup_ws(tmp_path, make_workspace, make_change)
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
        assert result.status == "ok"

    def test_go_generate_called_when_gate_set(
        self, tmp_path: Path, make_workspace: Any, make_change: Any, make_proc: Any
    ) -> None:
        """go generate IS invoked when REPO_OPS_REMEDIATE_GO_GENERATE=1."""
        result = self._setup_ws(tmp_path, make_workspace, make_change)
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
                return_value=make_proc(0),
            ) as mock_run,
        ):
            go_rem.run_remediations(result, apply=True, skip=set())

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] in called_cmds, (
            "go generate must run when REPO_OPS_REMEDIATE_GO_GENERATE=1"
        )


class TestGoGenerateDispatchPathGate:
    """Production path: orchestration remediations proxy / core.dispatch must gate go_generate."""

    def _setup_ws(
        self, tmp_path: Path, make_workspace: Any, make_change: Any
    ) -> WorkspaceResult:
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (tmp_path / "gen.go").write_text(
            "package main\n//go:generate stringer -type=Foo\n"
        )
        result = _go_result(tmp_path, make_workspace)
        result.changes = [make_change("golang.org/x/tools")]
        return result

    def test_core_dispatch_skips_go_generate_when_gate_off(
        self, tmp_path: Path, make_workspace: Any, make_change: Any
    ) -> None:
        result = self._setup_ws(tmp_path, make_workspace, make_change)
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
            patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run,
            patch.object(go_rem.shutil, "which", return_value="/usr/bin/go"),
        ):
            core.dispatch(result, apply=True, skip=set(), _registry=[fake_hook])

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] not in called_cmds

    def test_core_dispatch_runs_go_generate_when_gate_set(
        self, tmp_path: Path, make_workspace: Any, make_change: Any, make_proc: Any
    ) -> None:
        result = self._setup_ws(tmp_path, make_workspace, make_change)
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
                return_value=make_proc(0),
            ) as mock_run,
            patch.object(go_rem.shutil, "which", return_value="/usr/bin/go"),
        ):
            core.dispatch(result, apply=True, skip=set(), _registry=[fake_hook])

        called_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["go", "generate", "./..."] in called_cmds

    def test_orchestration_proxy_skips_go_generate_when_gate_off(
        self, tmp_path: Path, make_workspace: Any, make_change: Any
    ) -> None:
        """orchestration.remediations.run_remediations routes through dispatch gate."""
        from repo_ops_upgrade.orchestration import remediations as orch_rem

        result = self._setup_ws(tmp_path, make_workspace, make_change)
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
            patch("repo_ops_upgrade_remediations_go.remediations.run") as mock_run,
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
        self, tmp_path: Path, make_workspace: Any, make_change: Any, make_proc: Any
    ) -> None:
        """orchestration.remediations proxy respects REPO_OPS_REMEDIATE_GO_GENERATE=1."""
        from repo_ops_upgrade.orchestration import remediations as orch_rem

        result = self._setup_ws(tmp_path, make_workspace, make_change)
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
                return_value=make_proc(0),
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


@dataclass(frozen=True)
class _FmtCase:
    ecosystem: str
    manager: str
    manifest_name: str
    manifest_before: str
    manifest_after: str  # a same-language, source-untouched "bump"
    changed_name: str  # may include a subdir, e.g. "src/main.rs"
    changed_before: str
    changed_after: str
    unchanged_name: str
    unchanged_content: str
    change_pkg: str
    apply_fn: Any
    run_patch_target: str
    no_op_detail: str
    tool_argv0: str
    extra_no_op_file: tuple[str, str] | None = None  # (name, content), e.g. deno.lock


_FMT_CASES = [
    _FmtCase(
        ecosystem="go",
        manager="go",
        manifest_name="go.mod",
        manifest_before="module example.com/m\n\ngo 1.21\n",
        manifest_after="module example.com/m\n\ngo 1.21\n\nrequire golang.org/x/net v0.20.0\n",
        changed_name="main.go",
        changed_before="package main\nfunc main() {}\n",
        changed_after='package main\nimport "fmt"\nfunc main() { fmt.Println("hi") }\n',
        unchanged_name="other.go",
        unchanged_content="package main\nfunc other() {}\n",
        change_pkg="golang.org/x/net",
        apply_fn=go_rem.apply_gofmt,
        run_patch_target="repo_ops_upgrade_remediations_go.remediations.run",
        no_op_detail="no changed .go source files",
        tool_argv0="gofmt",
    ),
    _FmtCase(
        ecosystem="rust",
        manager="cargo",
        manifest_name="Cargo.toml",
        manifest_before="[package]\nname = 'foo'\nversion = '0.1.0'\n",
        manifest_after="[package]\nname = 'foo'\nversion = '0.1.0'\n\n[dependencies]\nserde = '1.0'\n",
        changed_name="src/main.rs",
        changed_before="fn main() {}\n",
        changed_after='fn main() {\n    println!("hello");\n}\n',
        unchanged_name="src/lib.rs",
        unchanged_content="pub fn helper() {}\n",
        change_pkg="serde",
        apply_fn=rust_rem.apply_cargo_fix,
        run_patch_target="repo_ops_upgrade_remediations_rust.remediations.run",
        no_op_detail="no changed .rs source files",
        tool_argv0="cargo",
    ),
    _FmtCase(
        ecosystem="deno",
        manager="deno",
        manifest_name="deno.json",
        manifest_before="{}\n",
        manifest_after='{"tasks": {}}\n',
        changed_name="main.ts",
        changed_before="console.log('a');\n",
        changed_after='console.log("changed");\n',
        unchanged_name="other.ts",
        unchanged_content="console.log('b');\n",
        change_pkg="std",
        apply_fn=deno_rem.apply_deno_fix,
        run_patch_target="repo_ops_upgrade_remediations_deno.remediations.run",
        no_op_detail="no changed source files",
        tool_argv0="deno",
        extra_no_op_file=("deno.lock", "{}\n"),
    ),
]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.parametrize("case", _FMT_CASES, ids=lambda c: c.ecosystem)
def test_fmt_targets_only_changed_source_files(
    tmp_path: Path,
    make_workspace: Any,
    make_change: Any,
    make_proc: Any,
    case: _FmtCase,
) -> None:
    """<tool> fmt targets only the files git diff reports as changed, not the whole tree."""
    _git_init(tmp_path)
    _write(tmp_path / case.manifest_name, case.manifest_before)
    changed_path = tmp_path / case.changed_name
    _write(changed_path, case.changed_before)
    _write(tmp_path / case.unchanged_name, case.unchanged_content)
    _git_commit_all(tmp_path)

    changed_path.write_text(case.changed_after)

    ws = make_workspace(
        tmp_path, ecosystem=case.ecosystem, manager=case.manager, label="test"
    )
    ctx: dict[str, Any] = {"changes": [make_change(case.change_pkg)], "apply": True}

    with patch(case.run_patch_target, return_value=make_proc(0)) as mock_run:
        ok, detail = case.apply_fn(ws, ctx)

    assert ok is True
    cmd = mock_run.call_args[0][0]
    changed_basename = Path(case.changed_name).name
    unchanged_basename = Path(case.unchanged_name).name
    assert any(changed_basename in arg for arg in cmd), (
        f"{case.tool_argv0} should target {changed_basename}; got {cmd}"
    )
    assert not any(unchanged_basename in arg for arg in cmd), (
        f"{case.tool_argv0} must not target {unchanged_basename}; got {cmd}"
    )

    # gofmt alone echoes its actual argv in the success detail (cargo/deno fmt
    # always return a fixed "<tool> fmt (ok)" regardless of which files ran).
    if case.ecosystem == "go":
        assert changed_basename in detail
        assert detail != "gofmt -w . (ok)"


@pytest.mark.parametrize("case", _FMT_CASES, ids=lambda c: c.ecosystem)
def test_fmt_no_op_when_only_manifest_changed(
    tmp_path: Path, make_workspace: Any, make_change: Any, case: _FmtCase
) -> None:
    """<tool> fmt is a no-op when only the manifest/lockfile changed, not source."""
    _git_init(tmp_path)
    _write(tmp_path / case.manifest_name, case.manifest_before)
    _write(tmp_path / case.changed_name, case.changed_before)
    _git_commit_all(tmp_path)

    (tmp_path / case.manifest_name).write_text(case.manifest_after)
    if case.extra_no_op_file is not None:
        name, content = case.extra_no_op_file
        (tmp_path / name).write_text(content)

    ws = make_workspace(
        tmp_path, ecosystem=case.ecosystem, manager=case.manager, label="test"
    )
    ctx: dict[str, Any] = {"changes": [make_change(case.change_pkg)], "apply": True}

    with patch(case.run_patch_target) as mock_run:
        ok, detail = case.apply_fn(ws, ctx)

    assert ok is True
    assert case.no_op_detail in detail
    assert not any(
        c[0][0] and c[0][0][0] == case.tool_argv0 for c in mock_run.call_args_list
    ), f"{case.tool_argv0} must not run when no source files changed"


# ══════════════════════════════════════════════════════════════════════════════
# Defect 3 — tool-missing → SKIP, not fail
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _MissingToolCase:
    rem: Any
    ecosystem: str
    manager: str
    manifest_name: str
    manifest_content: str
    detect_fn: str
    report_fn: str
    apply_fn: str
    hook_name: str
    change_pkg: str


_MISSING_TOOL_CASES = [
    _MissingToolCase(
        go_rem,
        "go",
        "go",
        "go.mod",
        "module example.com/m\n",
        "detect_gofmt",
        "report_gofmt",
        "apply_gofmt",
        "gofmt",
        "pkg",
    ),
    _MissingToolCase(
        rust_rem,
        "rust",
        "cargo",
        "Cargo.toml",
        "[package]\nname = 'foo'\n",
        "detect_cargo_fix",
        "report_cargo_fix",
        "apply_cargo_fix",
        "cargo_fix",
        "serde",
    ),
    _MissingToolCase(
        py_rem,
        "py",
        "uv",
        "ruff.toml",
        "line-length = 88\n",
        "detect_ruff",
        "report_ruff",
        "apply_ruff",
        "ruff_fix",
        "httpx",
    ),
]


class TestToolMissingYieldsSkip:
    """A missing remediation tool must never fail an otherwise-successful apply."""

    @pytest.mark.parametrize("case", _MISSING_TOOL_CASES, ids=lambda c: c.ecosystem)
    def test_detect_false_when_tool_missing(
        self,
        tmp_path: Path,
        make_workspace: Any,
        make_change: Any,
        case: _MissingToolCase,
    ) -> None:
        """detect_<tool> returns False when the tool binary is not installed."""
        (tmp_path / case.manifest_name).write_text(case.manifest_content)
        ws = make_workspace(
            tmp_path, ecosystem=case.ecosystem, manager=case.manager, label="test"
        )
        ctx: dict[str, Any] = {"changes": [make_change(case.change_pkg)], "apply": True}

        with patch(f"{case.rem.__name__}.shutil") as mock_sh:
            mock_sh.which.return_value = None
            detected = getattr(case.rem, case.detect_fn)(ws, ctx)

        assert detected is False

    @pytest.mark.parametrize("case", _MISSING_TOOL_CASES, ids=lambda c: c.ecosystem)
    def test_missing_tool_workspace_apply_stays_successful(
        self,
        tmp_path: Path,
        make_workspace: Any,
        make_change: Any,
        case: _MissingToolCase,
    ) -> None:
        """result.status stays 'ok' when the tool is absent (detect silently skips it)."""
        (tmp_path / case.manifest_name).write_text(case.manifest_content)
        ws = make_workspace(
            tmp_path, ecosystem=case.ecosystem, manager=case.manager, label="test"
        )
        result = WorkspaceResult(workspace=ws)
        result.changes = [make_change(case.change_pkg)]

        fake = case.rem.RemediationHook(
            case.hook_name,
            getattr(case.rem, case.detect_fn),
            getattr(case.rem, case.report_fn),
            getattr(case.rem, case.apply_fn),
        )
        with (
            patch.object(case.rem, "REMEDIATIONS", [fake]),
            patch(f"{case.rem.__name__}.shutil") as mock_sh,
        ):
            mock_sh.which.return_value = None
            case.rem.run_remediations(result, apply=True, skip=set())

        assert result.status == "ok"
        assert result.remediations == []

    # -- FileNotFoundError path in run_hooks ----------------------------------

    def test_file_not_found_in_apply_yields_skip_not_fail(
        self, tmp_path: Path, make_workspace: Any
    ) -> None:
        """FileNotFoundError raised by apply sets status='skip', not 'fail'."""
        ws = make_workspace(tmp_path)
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

    def test_go_generate_missing_tool_stays_successful(
        self, tmp_path: Path, make_workspace: Any, make_change: Any
    ) -> None:
        """Missing 'go' binary: detect_go_generate returns False, result stays ok."""
        (tmp_path / "go.mod").write_text("module example.com/m\n")
        (tmp_path / "gen.go").write_text(
            "package main\n//go:generate stringer -type=Foo\n"
        )
        ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="test")
        ctx: dict[str, Any] = {"changes": [make_change("pkg")], "apply": True}

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
