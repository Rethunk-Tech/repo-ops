"""Shared uv.lock check/apply helpers (used by py-uv and py-hatch)."""

from __future__ import annotations

import re

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.process import run
from repo_ops_lockfiles.parsers import toml_lock_versions
from repo_ops_models import Change, Workspace
from repo_ops_py.holds import filter_held_changes as _filter_held_changes
from repo_ops_py.version_ceiling import enforce_version_ceilings

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# uv (and hatch, which delegates here) has no per-package targeted install —
# ``uv lock --upgrade`` regenerates the whole lockfile — so unlike the JS
# ecosystems, this clamp-down is the *only* enforcement mechanism, not a
# safety net.


def _uv_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from uv.lock's ``[[package]]`` entries."""
    return toml_lock_versions(ws.path / "uv.lock")


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Post-apply lock-only clamp: ``uv lock --upgrade-package <pkg><=ceiling``.

    Also catches a hold added after the package was already locked above the
    ceiling: ``uv lock --upgrade --dry-run`` only reports packages that would
    move, so a package already past a brand-new ceiling never appears in
    ``check_uv_lock``'s changes.
    """
    enforce_version_ceilings(
        ws,
        tool="uv",
        manifest_name="pyproject.toml",
        lock_versions=_uv_lock_versions,
        clamp_command=lambda name, spec: ["uv", "lock", "--upgrade-package", spec],
        run=run,
    )


def check_uv_lock(ws: Workspace) -> list[Change]:
    """Run ``uv lock --upgrade --dry-run`` and return the detected changes."""
    proc = run(
        ["uv", "lock", "--upgrade", "--dry-run"],
        ws.path,
        timeout=TIMEOUT_CHECK,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if "would upgrade" in line.lower() or "->" in line:
            # Allow pre-release / local identifiers (e.g. 1.2.3a1, 1.0.0+local).
            m = re.search(
                r"(\S+)\s+([0-9][^\s]*)\s*->\s*([0-9][^\s]*)",
                line,
            )
            if m:
                changes.append(Change(m.group(1), m.group(2), m.group(3)))
    if not changes and "would change" in (proc.stdout + proc.stderr).lower():
        changes.append(Change("(lockfile)", "current", "upgraded"))
    return _filter_held_changes(ws, changes)


def apply_uv_lock(ws: Workspace) -> None:
    """Run ``uv lock --upgrade`` then ``uv sync``."""
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")
    _enforce_version_ceilings(ws)
