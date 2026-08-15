"""Shared uv.lock check/apply helpers (used by py-uv and py-hatch)."""

from __future__ import annotations

import re

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.holds import HoldRule, resolve_package_hold
from repo_ops_core.process import run
from repo_ops_core.version_utils import hold_ceiling_spec, version_satisfies_ceiling
from repo_ops_models import Change, Workspace
from repo_ops_py.holds import filter_held_changes as _filter_held_changes

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# uv (and hatch, which delegates here) has no per-package targeted install —
# ``uv lock --upgrade`` regenerates the whole lockfile — so unlike the JS
# ecosystems, this clamp-down is the *only* enforcement mechanism, not a
# safety net.


def _resolve_pkg_hold(ws: Workspace, package: str) -> HoldRule | None:
    return resolve_package_hold(ws, package)


def _uv_lock_versions(ws: Workspace) -> dict[str, str]:
    """name → resolved version, parsed from uv.lock's ``[[package]]`` entries."""
    data = read_toml_file(ws.path / "uv.lock") or {}
    out: dict[str, str] = {}
    for pkg in data.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version:
            out[str(name)] = str(version)
    return out


def _enforce_version_ceilings(ws: Workspace) -> None:
    """Post-apply lock-only clamp: correct any resolved package above its ceiling.

    Reads every package's resolved version from ``uv.lock`` and, for those
    with an active hold rule the resolved version violates, runs
    ``uv lock --upgrade-package <pkg><=ceiling>`` so only the lockfile changes.
    Raises if the correction still can't satisfy the ceiling — a hold that
    silently didn't hold is worse than a loud failure. Also catches a hold
    added after the package was already locked above the ceiling (``uv lock
    --upgrade --dry-run`` only reports packages that would move, so a package
    already resolved past a brand-new ceiling never appears in
    ``check_uv_lock``'s changes).
    """
    versions = _uv_lock_versions(ws)
    if not versions:
        return
    for name, resolved in versions.items():
        rule = _resolve_pkg_hold(ws, name)
        if rule is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        spec = f"{name}{hold_ceiling_spec(rule.max_version)}"
        proc = run(
            ["uv", "lock", "--upgrade-package", spec],
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"(uv): {proc.stderr.strip()}"
            )
        landed = _uv_lock_versions(ws).get(name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {name} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} (uv)"
            )


def check_uv_lock(ws: Workspace) -> list[Change]:
    """Run ``uv lock --upgrade --dry-run`` and return the detected changes."""
    proc = run(
        ["uv", "lock", "--upgrade", "--dry-run"],
        ws.path,
        timeout=resolve_timeout_check(),
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
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=resolve_timeout_apply())
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")
    _enforce_version_ceilings(ws)
