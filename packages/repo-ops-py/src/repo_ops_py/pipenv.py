"""pipenv upgrade check/apply."""

from __future__ import annotations

import shutil
import sys

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_pipenv(ws: Workspace) -> list[Change]:
    """Return pending upgrades for a pipenv-managed workspace.

    Uses ``pipenv run pip list --outdated --format=columns`` to enumerate
    packages whose installed version lags the latest available on PyPI.
    The first two lines are header/separator rows and are skipped.

    Falls back to an empty list with a WARN when ``pipenv`` is absent.
    """
    if not shutil.which("pipenv"):
        print(f"WARN {ws.path}: pipenv not on PATH", file=sys.stderr)
        return []
    proc = run(
        ["pipenv", "run", "pip", "list", "--outdated", "--format=columns"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or "pipenv run pip list --outdated failed"
        )
    changes: list[Change] = []
    for line in proc.stdout.splitlines()[2:]:  # skip "Package  Version  Latest  Type" header
        parts = line.split()
        if len(parts) >= 3:
            changes.append(Change(parts[0], parts[1], parts[2]))
    return changes


def apply_pipenv(ws: Workspace) -> None:
    """Run ``pipenv update`` to upgrade all packages and regenerate Pipfile.lock."""
    if not shutil.which("pipenv"):
        raise RuntimeError(f"pipenv not on PATH for {ws.path}")
    proc = run(["pipenv", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pipenv update failed")
