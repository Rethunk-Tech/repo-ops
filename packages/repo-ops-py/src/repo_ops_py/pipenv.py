"""pipenv upgrade check/apply."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_pipenv(ws: Workspace) -> list[Change]:
    """Return pending upgrades for a pipenv-managed workspace.

    Uses the venv's own pip directly (``pip list --outdated --format=json``)
    to enumerate packages whose installed version lags the latest on PyPI.
    Never creates the virtualenv — probes for an existing venv via
    ``pipenv --venv`` first; returns [] with a WARN when none is found so
    that plan is always read-only.

    Falls back to an empty list with a WARN when ``pipenv`` is absent.
    """
    if not shutil.which("pipenv"):
        print(f"WARN {ws.path}: pipenv not on PATH", file=sys.stderr)
        return []
    # Read-only probe: get venv path without creating one.
    probe = run(["pipenv", "--venv"], ws.path, timeout=10)
    if probe.returncode != 0:
        print(
            f"WARN {ws.path}: pipenv venv not initialised; run 'pipenv install' first",
            file=sys.stderr,
        )
        return []
    venv_path = probe.stdout.strip()
    # Derive pip from the venv path; fall back to bare "pip" when the probe
    # returned an unusable path (e.g. in unit tests with a mocked run).
    if venv_path and Path(venv_path).is_absolute():
        pip_cmd = str(Path(venv_path) / "bin" / "pip")
    else:
        pip_cmd = "pip"
    proc = run(
        [pip_cmd, "list", "--outdated", "--format=json"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip list --outdated failed")
    changes: list[Change] = []
    try:
        pkgs = json.loads(proc.stdout)
        for entry in pkgs:
            changes.append(
                Change(entry["name"], entry["version"], entry["latest_version"])
            )
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fallback: column format (older pip versions without JSON support)
        for line in proc.stdout.splitlines()[2:]:
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
