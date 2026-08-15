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
from pathlib import Path
from typing import Any

from repo_ops_core.process import run
from repo_ops_models import Workspace
from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    changed_files_since_head,
    make_run_remediations,
    register,
    stderr_tail,
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


def _changed_deno_files(ws: Workspace) -> list[str] | None:
    """Return absolute paths of Deno-formattable files changed since HEAD in *ws*.

    Config/lock bumps are not "source" reformats — excluded so pure dep
    updates that only touch deno.json / deno.lock become a no-op.
    """
    return changed_files_since_head(
        ws, DENO_FMT_EXTENSIONS, exclude_names=DENO_CONFIG_NAMES
    )


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
    err = stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"deno fmt (failed: {err})"


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [
    RemediationHook("deno_fix", detect_deno_fix, report_deno_fix, apply_deno_fix),
]

# Register all Deno hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


run_remediations = make_run_remediations(lambda: REMEDIATIONS)
