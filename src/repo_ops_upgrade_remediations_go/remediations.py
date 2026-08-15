"""Post-upgrade remediation hooks for the Go ecosystem.

Gating: every hook checks ``ws.ecosystem == "go"`` first so it is always a
clean no-op for node/rust/py/container workspaces.  Each hook additionally
requires a ``go.mod`` file at the workspace root — workspaces without one are
silently skipped.

Included hooks
--------------
gofmt
    Run ``gofmt -w <changed-files>`` after a dependency bump to normalize
    formatting only on the Go source files touched by the bump.  Falls back
    to ``gofmt -w .`` when git is unavailable.  Safe to apply
    unconditionally when go.mod is present.
go_generate
    Run ``go generate ./...`` after a dependency bump to refresh generated
    code (stringer, protoc-gen-go, wire, mockgen, etc.).  **Opt-in only:**
    set ``REPO_OPS_REMEDIATE_GO_GENERATE=1`` to enable; default-off because
    ``//go:generate`` directives may execute arbitrary code from
    freshly-cloned repositories.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from repo_ops_core.process import run
from repo_ops_models import Workspace
from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    make_fmt_hook,
    register,
    run_hooks,
    stderr_tail,
)

# Directories that should never be scanned for //go:generate directives.
_SKIP_DIRS: frozenset[str] = frozenset(
    {"vendor", ".git", "node_modules", "testdata", "_vendor"}
)


def _has_go_mod(path: Path) -> bool:
    """True when *path* contains a go.mod file."""
    return (path / "go.mod").exists()


def _has_go_generate(path: Path) -> bool:
    """True when any .go file under *path* (outside skip dirs) has //go:generate."""
    try:
        for go_file in path.rglob("*.go"):
            # Skip vendored / generated / VCS-internal trees.
            try:
                parts = set(go_file.relative_to(path).parts)
            except ValueError:
                continue
            if parts & _SKIP_DIRS:
                continue
            try:
                if "//go:generate" in go_file.read_text(encoding="utf-8"):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


# ── gofmt hook ───────────────────────────────────────────────────────────────


def _gofmt_which(tool: str) -> str | None:
    # Thin wrapper (rather than passing shutil.which directly) so that
    # patching this module's `shutil` name still reaches the check made
    # from inside the shared factory.
    return shutil.which(tool)


def _gofmt_run(cmd: list[str], cwd: Path, timeout: float) -> Any:
    # Thin wrapper so that patching this module's `run` name still reaches
    # the subprocess call made from inside the shared factory.
    return run(cmd, cwd, timeout=timeout)


def _gofmt_argv(files: list[str] | None) -> list[str]:
    return ["gofmt", "-w", "."] if files is None else ["gofmt", "-w", *files]


_gofmt_hook = make_fmt_hook(
    hook_id="gofmt",
    ecosystem="go",
    ecosystem_label="Go",
    config_names=("go.mod",),
    extensions=(".go",),
    tool="gofmt",
    which_fn=_gofmt_which,
    run_fn=_gofmt_run,
    build_argv=_gofmt_argv,
    fail_label="gofmt",
    no_changes_apply_detail="gofmt: no changed .go source files (ok)",
    detailed_report=True,
    no_changes_plan_detail=(
        "REMEDIATE gofmt: no changed .go source files (would skip formatting)"
    ),
)
detect_gofmt = _gofmt_hook.detect
report_gofmt = _gofmt_hook.report
apply_gofmt = _gofmt_hook.apply


# ── go_generate hook ─────────────────────────────────────────────────────────


def detect_go_generate(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when go generate remediation should run.

    Applies only to Go (``ecosystem == "go"``) workspaces that have a
    ``go.mod`` AND at least one ``//go:generate`` directive in source
    (outside vendor/generated trees).  Triggered when at least one dependency
    changed (apply) or in plan mode.  Skipped when ``go`` is not installed.

    Note: the hook is gated behind ``REPO_OPS_REMEDIATE_GO_GENERATE=1`` at
    the ``run_remediations`` level; this detect function is left opt-out-free
    so that plan-mode reporting works correctly when the gate is enabled.
    """
    if ws.ecosystem != "go":
        return False
    if not _has_go_mod(ws.path):
        return False
    if shutil.which("go") is None:
        return False  # tool not available — skip, do not fail
    if not _has_go_generate(ws.path):
        return False
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_go_generate(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [
        "REMEDIATE go_generate: would run go generate ./... (refresh generated code)"
    ]


def apply_go_generate(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run ``go generate ./...`` in the workspace root."""
    proc = run(["go", "generate", "./..."], ws.path, timeout=300)
    if proc.returncode == 0:
        return True, "go generate ./... (ok)"
    err = stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"go generate (failed: {err})"


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [
    _gofmt_hook,
    RemediationHook(
        "go_generate", detect_go_generate, report_go_generate, apply_go_generate
    ),
]

# Register all Go hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


def run_remediations(
    result: Any,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    """Run Go remediation hooks against *result*.

    Uses the local ``REMEDIATIONS`` list so tests can patch it independently
    of the global registry.

    ``go_generate`` is **default-off**: it is added to the effective skip set
    unless ``REPO_OPS_REMEDIATE_GO_GENERATE=1`` is set in the environment.
    This prevents arbitrary ``//go:generate`` directives from executing as
    part of an automated fleet apply.
    """
    effective_skip = set(skip)
    if os.environ.get("REPO_OPS_REMEDIATE_GO_GENERATE", "") != "1":
        effective_skip.add("go_generate")
    run_hooks(REMEDIATIONS, result, apply=apply, skip=effective_skip)
