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
from repo_ops_upgrade_remediations_core.remediations import (
    RemediationHook,
    make_fmt_hook,
    make_run_remediations,
    register,
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


# ── deno_fix hook ────────────────────────────────────────────────────────────


def _deno_fix_which(tool: str) -> str | None:
    # Thin wrapper (rather than passing shutil.which directly) so that
    # patching this module's `shutil` name still reaches the check made
    # from inside the shared factory.
    return shutil.which(tool)


def _deno_fix_run(cmd: list[str], cwd: Path, timeout: float) -> Any:
    # Thin wrapper so that patching this module's `run` name still reaches
    # the subprocess call made from inside the shared factory.
    return run(cmd, cwd, timeout=timeout)


def _deno_fix_argv(files: list[str] | None) -> list[str]:
    return ["deno", "fmt"] if files is None else ["deno", "fmt", *files]


_deno_fix_hook = make_fmt_hook(
    hook_id="deno_fix",
    ecosystem="deno",
    ecosystem_label="Deno",
    config_names=DENO_CONFIG_NAMES,
    extensions=DENO_FMT_EXTENSIONS,
    tool="deno",
    which_fn=_deno_fix_which,
    run_fn=_deno_fix_run,
    build_argv=_deno_fix_argv,
    fail_label="deno fmt",
    no_changes_apply_detail="deno fmt: no changed source files (ok)",
    plan_message="REMEDIATE deno_fix: would run deno fmt (normalize Deno formatting)",
    success_label="deno fmt (ok)",
)
detect_deno_fix = _deno_fix_hook.detect
report_deno_fix = _deno_fix_hook.report
apply_deno_fix = _deno_fix_hook.apply


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [_deno_fix_hook]

# Register all Deno hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


run_remediations = make_run_remediations(lambda: REMEDIATIONS)
