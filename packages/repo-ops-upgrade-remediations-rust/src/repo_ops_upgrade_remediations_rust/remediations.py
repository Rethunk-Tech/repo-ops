"""Post-upgrade remediation hooks for the Rust ecosystem.

Gating: every hook checks ``ws.ecosystem == "rust"`` first so it is always a
clean no-op for node/go/py/container workspaces.  Each hook additionally
requires a ``Cargo.toml`` file at the workspace root — workspaces without one
are silently skipped.

Included hooks
--------------
cargo_fix
    Run ``cargo fmt`` after a dependency bump to normalize formatting across
    all Rust source files.  ``cargo fmt`` is the safe, formatting-only step;
    it is always applicable once Cargo.toml is present and skips cleanly on
    non-Rust workspaces.
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


def _has_cargo_toml(path: Path) -> bool:
    """True when *path* contains a Cargo.toml file."""
    return (path / "Cargo.toml").exists()


def _stderr_tail(text: str, *, max_chars: int = 200) -> str:
    s = (text or "").strip()
    if not s:
        return "non-zero exit"
    return s if len(s) <= max_chars else s[-max_chars:]


# ── cargo_fix hook ───────────────────────────────────────────────────────────


def detect_cargo_fix(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when cargo_fix remediation should run.

    Applies only to Rust (``ecosystem == "rust"``) workspaces that have a
    ``Cargo.toml`` at the workspace root.  Triggered when at least one
    dependency changed (apply) or in plan mode with any candidate.
    """
    if ws.ecosystem != "rust":
        return False
    if not _has_cargo_toml(ws.path):
        return False
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_cargo_fix(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [
        "REMEDIATE cargo_fix: would run cargo fmt (normalize Rust formatting)"
    ]


def apply_cargo_fix(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run ``cargo fmt`` in the workspace root.

    ``cargo fmt`` is the safe formatting step run after a dependency bump.
    It normalises Rust source style without modifying program semantics.
    """
    proc = run(["cargo", "fmt"], ws.path, timeout=120)
    if proc.returncode == 0:
        return True, "cargo fmt (ok)"
    err = _stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"cargo fmt (failed: {err})"


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [
    RemediationHook("cargo_fix", detect_cargo_fix, report_cargo_fix, apply_cargo_fix),
]

# Register all Rust hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


def run_remediations(
    result: Any,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    """Run Rust remediation hooks against *result*.

    Uses the local ``REMEDIATIONS`` list so tests can patch it independently
    of the global registry.
    """
    run_hooks(REMEDIATIONS, result, apply=apply, skip=skip)
