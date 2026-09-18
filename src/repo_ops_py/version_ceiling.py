"""Shared post-apply hold-ceiling clamp for pip ecosystems with no
per-package targeted install (poetry, pdm, pipenv) — their update command
regenerates the whole lockfile, so this clamp-down is the only enforcement
mechanism, including for a hold added after the package was already locked
past it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from repo_ops_core.constants import TIMEOUT_APPLY
from repo_ops_core.holds import resolve_package_hold
from repo_ops_core.version_utils import hold_ceiling_spec, version_satisfies_ceiling
from repo_ops_models import Workspace


def enforce_version_ceilings(
    ws: Workspace,
    *,
    tool: str,
    manifest_name: str,
    lock_versions: Callable[[Workspace], dict[str, str]],
    clamp_command: Callable[[str, str], list[str]],
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Correct any locked package resolved above its ceiling.

    ``lock_versions`` reads the manager's lockfile, ``clamp_command`` builds
    the manager's targeted-install command for a package + ceiling spec, and
    ``run`` is the caller's own process runner (passed through rather than
    imported here so callers can still patch it at their own module).
    """
    versions = lock_versions(ws)
    if not versions:
        return
    manifest = ws.path / manifest_name
    for name, resolved in versions.items():
        rule = resolve_package_hold(ws, name)
        if rule is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        spec = f"{name}{hold_ceiling_spec(rule.max_version)}"
        original: bytes | None = manifest.read_bytes() if manifest.exists() else None
        try:
            proc = run(
                clamp_command(name, spec),
                ws.path,
                timeout=TIMEOUT_APPLY,
            )
        finally:
            if original is not None:
                manifest.write_bytes(original)
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"({tool}): {proc.stderr.strip()}"
            )
        landed = lock_versions(ws).get(name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {name} still at {landed or resolved} after clamp "
                f"attempt, expected <= {rule.max_version} ({tool})"
            )
