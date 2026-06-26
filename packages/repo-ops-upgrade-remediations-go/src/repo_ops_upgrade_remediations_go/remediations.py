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
import subprocess
from pathlib import Path
from typing import Any

from repo_ops_core.process import run
from repo_ops_models import Workspace
from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    register,
    run_hooks,
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


def _stderr_tail(text: str, *, max_chars: int = 200) -> str:
    s = (text or "").strip()
    if not s:
        return "non-zero exit"
    return s if len(s) <= max_chars else s[-max_chars:]


def _changed_go_files(ws: Workspace) -> list[str] | None:
    """Return absolute paths of .go files changed since the last commit in *ws*.

    Only files within *ws.path* are returned.  Returns ``None`` when git is
    unavailable or this is not a git repository — the caller should fall back
    to whole-workspace formatting.  Returns an empty list when git worked but
    no ``.go`` source files changed (typical for a pure dep bump that only
    touched ``go.mod``/``go.sum``).

    Uses ``subprocess`` directly (not the repo_ops_core.process.run helper)
    so that tests that mock the process helper can still exercise the fallback
    path without interference from git calls.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(ws.git_repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    changed: list[str] = []
    for rel in proc.stdout.splitlines():
        if not rel.endswith(".go"):
            continue
        abs_path = ws.git_repo / rel
        try:
            abs_path.relative_to(ws.path)
        except ValueError:
            continue
        if abs_path.exists():
            changed.append(str(abs_path))
    return changed


# ── gofmt hook ───────────────────────────────────────────────────────────────


def detect_gofmt(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when gofmt remediation should run.

    Applies only to Go (``ecosystem == "go"``) workspaces that have a
    ``go.mod`` at the workspace root.  Triggered when at least one dependency
    changed (apply) or in plan mode with any candidate.  Skipped when
    ``gofmt`` is not installed.
    """
    if ws.ecosystem != "go":
        return False
    if not _has_go_mod(ws.path):
        return False
    if shutil.which("gofmt") is None:
        return False  # tool not available — skip, do not fail
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_gofmt(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return ["REMEDIATE gofmt: would run gofmt -w . (normalize Go formatting)"]


def apply_gofmt(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run gofmt only on Go source files changed by the dependency bump.

    Scopes formatting to the ``.go`` files reported by ``git diff HEAD`` so
    that unrelated source files are never swept into the ``chore(deps)``
    commit.  Falls back to ``gofmt -w .`` when git is unavailable or this
    workspace is not in a git repository.
    """
    go_files = _changed_go_files(ws)
    if go_files is None:
        # git unavailable — fall back to whole-workspace formatting (original behaviour).
        cmd: list[str] = ["gofmt", "-w", "."]
    elif not go_files:
        # No .go source files changed — formatting is a no-op.
        return True, "gofmt: no changed .go source files (ok)"
    else:
        cmd = ["gofmt", "-w", *go_files]
    proc = run(cmd, ws.path, timeout=120)
    if proc.returncode == 0:
        return True, "gofmt -w . (ok)"
    err = _stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"gofmt (failed: {err})"


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
    err = _stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"go generate (failed: {err})"


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [
    RemediationHook("gofmt", detect_gofmt, report_gofmt, apply_gofmt),
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
