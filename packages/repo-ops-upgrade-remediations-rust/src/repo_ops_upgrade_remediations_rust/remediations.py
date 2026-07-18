"""Post-upgrade remediation hooks for the Rust ecosystem.

Gating: every hook checks ``ws.ecosystem == "rust"`` first so it is always a
clean no-op for node/go/py/container workspaces.  Each hook additionally
requires a ``Cargo.toml`` file at the workspace root — workspaces without one
are silently skipped.

Included hooks
--------------
cargo_fix
    Run ``cargo fmt`` (or ``cargo fmt -- <changed-files>``) after a dependency
    bump to normalize formatting.  Scoped to the Rust source files touched by
    the bump so that unrelated crates in a workspace are not reformatted and
    swept into the ``chore(deps)`` commit.  Falls back to ``cargo fmt`` when
    git is unavailable.  ``cargo fmt`` is the safe, formatting-only step;
    it is always applicable once Cargo.toml is present and skips cleanly on
    non-Rust workspaces.
"""

from __future__ import annotations

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
    stderr_tail,
)


def _has_cargo_toml(path: Path) -> bool:
    """True when *path* contains a Cargo.toml file."""
    return (path / "Cargo.toml").exists()


def _changed_rust_files(ws: Workspace) -> list[str] | None:
    """Return absolute paths of .rs files changed since the last commit in *ws*.

    Only files within *ws.path* are returned.  Returns ``None`` when git is
    unavailable or this is not a git repository — the caller should fall back
    to whole-workspace ``cargo fmt``.  Returns an empty list when git worked
    but no ``.rs`` source files changed (typical for a pure dep bump that
    only touched ``Cargo.toml``/``Cargo.lock``).

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
        if not rel.endswith(".rs"):
            continue
        abs_path = ws.git_repo / rel
        try:
            abs_path.relative_to(ws.path)
        except ValueError:
            continue
        if abs_path.exists():
            changed.append(str(abs_path))
    return changed


# ── cargo_fix hook ───────────────────────────────────────────────────────────


def detect_cargo_fix(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when cargo_fix remediation should run.

    Applies only to Rust (``ecosystem == "rust"``) workspaces that have a
    ``Cargo.toml`` at the workspace root.  Triggered when at least one
    dependency changed (apply) or in plan mode with any candidate.  Skipped
    when ``cargo`` is not installed.
    """
    if ws.ecosystem != "rust":
        return False
    if not _has_cargo_toml(ws.path):
        return False
    if shutil.which("cargo") is None:
        return False  # tool not available — skip, do not fail
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_cargo_fix(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return ["REMEDIATE cargo_fix: would run cargo fmt (normalize Rust formatting)"]


def apply_cargo_fix(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run cargo fmt on Rust source files changed by the dependency bump.

    Scopes formatting to the ``.rs`` files reported by ``git diff HEAD`` so
    that unrelated crates in a Cargo workspace are not reformatted and swept
    into the ``chore(deps)`` commit.  Falls back to ``cargo fmt`` (whole
    workspace) when git is unavailable or this workspace is not in a git
    repository.
    """
    rs_files = _changed_rust_files(ws)
    if rs_files is None:
        # git unavailable — fall back to whole-workspace formatting (original behaviour).
        proc = run(["cargo", "fmt"], ws.path, timeout=120)
    elif not rs_files:
        # No .rs source files changed — formatting is a no-op.
        return True, "cargo fmt: no changed .rs source files (ok)"
    else:
        # Pass specific files to rustfmt via cargo fmt's -- passthrough.
        proc = run(["cargo", "fmt", "--", *rs_files], ws.path, timeout=120)
    if proc.returncode == 0:
        return True, "cargo fmt (ok)"
    err = stderr_tail(proc.stderr or proc.stdout or "")
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
