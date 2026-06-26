"""Post-upgrade remediation hooks for the Go ecosystem.

Gating: every hook checks ``ws.ecosystem == "go"`` first so it is always a
clean no-op for node/rust/py/container workspaces.  Each hook additionally
requires a ``go.mod`` file at the workspace root — workspaces without one are
silently skipped.

Included hooks
--------------
gofmt
    Run ``gofmt -w .`` after a dependency bump to normalize formatting across
    all Go source files.  Safe to apply unconditionally when go.mod is present.
go_generate
    Run ``go generate ./...`` after a dependency bump to refresh generated
    code (stringer, protoc-gen-go, wire, mockgen, etc.).  Only triggered when
    a ``go:generate`` directive is detected in any ``.go`` file under the
    workspace root; skipped when none is found.
"""

from __future__ import annotations

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


def _has_go_mod(path: Path) -> bool:
    """True when *path* contains a go.mod file."""
    return (path / "go.mod").exists()


def _has_go_generate(path: Path) -> bool:
    """True when any .go file under *path* contains a //go:generate directive."""
    try:
        for go_file in path.rglob("*.go"):
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


# ── gofmt hook ───────────────────────────────────────────────────────────────


def detect_gofmt(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when gofmt remediation should run.

    Applies only to Go (``ecosystem == "go"``) workspaces that have a
    ``go.mod`` at the workspace root.  Triggered when at least one dependency
    changed (apply) or in plan mode with any candidate.
    """
    if ws.ecosystem != "go":
        return False
    if not _has_go_mod(ws.path):
        return False
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_gofmt(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return ["REMEDIATE gofmt: would run gofmt -w . (normalize Go formatting)"]


def apply_gofmt(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run ``gofmt -w .`` in the workspace root."""
    proc = run(["gofmt", "-w", "."], ws.path, timeout=120)
    if proc.returncode == 0:
        return True, "gofmt -w . (ok)"
    err = _stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"gofmt (failed: {err})"


# ── go_generate hook ─────────────────────────────────────────────────────────


def detect_go_generate(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when go generate remediation should run.

    Applies only to Go (``ecosystem == "go"``) workspaces that have a
    ``go.mod`` AND at least one ``//go:generate`` directive in source.
    Triggered when at least one dependency changed (apply) or in plan mode.
    """
    if ws.ecosystem != "go":
        return False
    if not _has_go_mod(ws.path):
        return False
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
    """
    run_hooks(REMEDIATIONS, result, apply=apply, skip=skip)
