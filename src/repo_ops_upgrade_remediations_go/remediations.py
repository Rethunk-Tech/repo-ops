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
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from repo_ops_core.process import run
from repo_ops_upgrade_remediations_core.remediations import (
    RemediationHook,
    make_fmt_hook,
    make_run_remediations,
    register,
)

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
    no_changes_plan_detail=("REMEDIATE gofmt: no changed .go source files (would skip formatting)"),
)
detect_gofmt = _gofmt_hook.detect
report_gofmt = _gofmt_hook.report
apply_gofmt = _gofmt_hook.apply


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [_gofmt_hook]

# Register all Go hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


run_remediations = make_run_remediations(lambda: REMEDIATIONS)
