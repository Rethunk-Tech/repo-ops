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
from pathlib import Path
from typing import Any

from repo_ops_core.process import run
from repo_ops_upgrade_remediations_core.remediations import (
    RemediationHook,
    make_fmt_hook,
    make_run_remediations,
    register,
)

# ── cargo_fix hook ───────────────────────────────────────────────────────────


def _cargo_fix_which(tool: str) -> str | None:
    # Thin wrapper (rather than passing shutil.which directly) so that
    # patching this module's `shutil` name still reaches the check made
    # from inside the shared factory.
    return shutil.which(tool)


def _cargo_fix_run(cmd: list[str], cwd: Path, timeout: float) -> Any:
    # Thin wrapper so that patching this module's `run` name still reaches
    # the subprocess call made from inside the shared factory.
    return run(cmd, cwd, timeout=timeout)


def _cargo_fix_argv(files: list[str] | None) -> list[str]:
    return ["cargo", "fmt"] if files is None else ["cargo", "fmt", "--", *files]


_cargo_fix_hook = make_fmt_hook(
    hook_id="cargo_fix",
    ecosystem="rust",
    ecosystem_label="Rust",
    config_names=("Cargo.toml",),
    extensions=(".rs",),
    tool="cargo",
    which_fn=_cargo_fix_which,
    run_fn=_cargo_fix_run,
    build_argv=_cargo_fix_argv,
    fail_label="cargo fmt",
    no_changes_apply_detail="cargo fmt: no changed .rs source files (ok)",
    plan_message="REMEDIATE cargo_fix: would run cargo fmt (normalize Rust formatting)",
    success_label="cargo fmt (ok)",
)
detect_cargo_fix = _cargo_fix_hook.detect
report_cargo_fix = _cargo_fix_hook.report
apply_cargo_fix = _cargo_fix_hook.apply


# ── registry ─────────────────────────────────────────────────────────────────

REMEDIATIONS: list[RemediationHook] = [_cargo_fix_hook]

# Register all Rust hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


run_remediations = make_run_remediations(lambda: REMEDIATIONS)
