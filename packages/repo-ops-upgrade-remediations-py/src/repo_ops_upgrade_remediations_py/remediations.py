"""Post-upgrade remediation hooks for the Python (py) ecosystem.

Gating: every hook checks ``ws.ecosystem == "py"`` first so it is always a
clean no-op for node/go/rust/container workspaces.

Included hooks
--------------
ruff_fix
    Run ``ruff check --fix`` + ``ruff format`` after a dependency bump in a
    project that has a ruff configuration (``ruff.toml`` or ``[tool.ruff]``
    in ``pyproject.toml``).  Skipped when ruff is not configured or when the
    ``ruff`` binary is not installed.
"""

from __future__ import annotations

import shutil
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


def _has_ruff_config(path: Path) -> bool:
    """True when *path* contains a ruff configuration."""
    if (path / "ruff.toml").exists() or (path / ".ruff.toml").exists():
        return True
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            return "[tool.ruff]" in pyproject.read_text(encoding="utf-8")
        except OSError:
            return False
    return False


def detect_ruff(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when ruff remediation should run.

    Applies only to Python (``ecosystem == "py"``) workspaces that already
    have a ruff configuration.  The presence of any dependency change is
    sufficient trigger — a version bump may introduce new lint rules or
    formatting requirements that ruff can fix automatically.  Skipped when
    ``ruff`` is not installed.
    """
    if ws.ecosystem != "py":
        return False
    if not _has_ruff_config(ws.path):
        return False
    if shutil.which("ruff") is None:
        return False  # tool not available — skip, do not fail
    # Only trigger when something actually changed (apply) or when in plan mode
    # with at least one change candidate.
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_ruff(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [
        "REMEDIATE ruff: would run ruff check --fix (auto-fixable lint) + ruff format"
    ]


def apply_ruff(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run ``ruff check --fix`` then ``ruff format`` in the workspace root."""
    fix_proc = run(["ruff", "check", "--fix", "."], ws.path, timeout=120)
    fmt_proc = run(["ruff", "format", "."], ws.path, timeout=120)
    if fix_proc.returncode == 0 and fmt_proc.returncode == 0:
        return True, "ruff check --fix && ruff format (ok)"
    err_parts: list[str] = []
    if fix_proc.returncode != 0:
        err_parts.append(
            f"check --fix: {stderr_tail(fix_proc.stderr or fix_proc.stdout or '')}"
        )
    if fmt_proc.returncode != 0:
        err_parts.append(
            f"format: {stderr_tail(fmt_proc.stderr or fmt_proc.stdout or '')}"
        )
    return False, f"ruff (failed: {'; '.join(err_parts)})"


REMEDIATIONS: list[RemediationHook] = [
    RemediationHook("ruff_fix", detect_ruff, report_ruff, apply_ruff),
]

# Register all Python hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


def run_remediations(
    result: Any,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    """Run Python remediation hooks against *result*.

    Uses the local ``REMEDIATIONS`` list so tests can patch it independently
    of the global registry.
    """
    run_hooks(REMEDIATIONS, result, apply=apply, skip=skip)
