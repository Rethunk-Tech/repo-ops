"""Post-upgrade remediation hooks for the Deno ecosystem.

Gating: every hook checks ``ws.ecosystem == "deno"`` first so it is always a
clean no-op for node/go/py/rust/container workspaces.  Each hook additionally
requires a Deno config file (``deno.json``, ``deno.jsonc``, or ``deno.lock``)
at the workspace root — workspaces without one are silently skipped.

Included hooks
--------------
deno_fix
    Run ``deno fmt`` (scoped to the files touched by the bump when known)
    after a dependency bump to normalize formatting.  Scoped to the
    Deno-formattable files touched by the bump so that unrelated files in a
    workspace are not reformatted and swept into the ``chore(deps)`` commit.
    Falls back to whole-workspace ``deno fmt`` when git is unavailable.
    ``deno fmt`` is the safe, formatting-only step; it is always applicable
    once a Deno config file is present and skips cleanly on non-Deno
    workspaces.  ``deno lint --fix`` is intentionally NOT run here — only
    formatters run in this hook, mirroring the gofmt/cargo-fmt peers.
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
)

DENO_CONFIG_NAMES = ("deno.json", "deno.jsonc", "deno.lock")

# Extensions formatted by `deno fmt` by default.
DENO_FMT_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".jsonc",
    ".md",
)


def _has_deno_config(path: Path) -> bool:
    """True when *path* contains a Deno config file."""
    return any((path / name).exists() for name in DENO_CONFIG_NAMES)


def _stderr_tail(text: str, *, max_chars: int = 200) -> str:
    s = (text or "").strip()
    if not s:
        return "non-zero exit"
    return s if len(s) <= max_chars else s[-max_chars:]


def _changed_deno_files(ws: Workspace) -> list[str] | None:
    """Return absolute paths of Deno-formattable files changed since HEAD in *ws*.

    Only files within *ws.path* are returned.  Returns ``None`` when git is
    unavailable or this is not a git repository — the caller should fall back
    to whole-workspace ``deno fmt``.  Returns an empty list when git worked
    but no formattable source files changed (typical for a pure dep bump
    that only touched ``deno.json``/``deno.lock``).

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
        if not rel.endswith(DENO_FMT_EXTENSIONS):
            continue
        abs_path = ws.git_repo / rel
        try:
            abs_path.relative_to(ws.path)
        except ValueError:
            continue
        if abs_path.exists():
            changed.append(str(abs_path))
    return changed


# ── deno_fix hook ────────────────────────────────────────────────────────────


def detect_deno_fix(ws: Workspace, ctx: dict[str, Any]) -> bool:
    """Detect when deno_fix remediation should run.

    Applies only to Deno (``ecosystem == "deno"``) workspaces that have a
    Deno config file at the workspace root.  Triggered when at least one
    dependency changed (apply) or in plan mode with any candidate.  Skipped
    when ``deno`` is not installed.
    """
    if ws.ecosystem != "deno":
        return False
    if not _has_deno_config(ws.path):
        return False
    if shutil.which("deno") is None:
        return False  # tool not available — skip, do not fail
    changes = ctx.get("changes", [])
    return bool(changes) or not ctx.get("apply", True)


def report_deno_fix(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return ["REMEDIATE deno_fix: would run deno fmt (normalize Deno formatting)"]


def apply_deno_fix(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Run deno fmt on Deno source files changed by the dependency bump.

    Scopes formatting to the files reported by ``git diff HEAD`` so that
    unrelated files in a workspace are not reformatted and swept into the
    ``chore(deps)`` commit.  Falls back to ``deno fmt`` (whole workspace)
    when git is unavailable or this workspace is not in a git repository.
    """
    changed_files = _changed_deno_files(ws)
    if changed_files is None:
        # git unavailable — fall back to whole-workspace formatting (original behaviour).
        proc = run(["deno", "fmt"], ws.path, timeout=120)
    elif not changed_files:
        # No formattable source files changed — formatting is a no-op.
        return True, "deno fmt: no changed source files (ok)"
    else:
        proc = run(["deno", "fmt", *changed_files], ws.path, timeout=120)
    if proc.returncode == 0:
        return True, "deno fmt (ok)"
    err = _stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"deno fmt (failed: {err})"


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [
    RemediationHook("deno_fix", detect_deno_fix, report_deno_fix, apply_deno_fix),
]

# Register all Deno hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


def run_remediations(
    result: Any,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    """Run Deno remediation hooks against *result*.

    Uses the local ``REMEDIATIONS`` list so tests can patch it independently
    of the global registry.
    """
    run_hooks(REMEDIATIONS, result, apply=apply, skip=skip)
