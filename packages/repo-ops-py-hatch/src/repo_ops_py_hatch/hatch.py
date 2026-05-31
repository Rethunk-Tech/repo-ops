"""hatch upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY


def _check_uv_lock(ws: Workspace) -> list[Change]:
    proc = run(["uv", "lock", "--upgrade", "--dry-run"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if "would upgrade" in line.lower() or "->" in line:
            m = re.search(r"(\S+)\s+([\d.]+)\s*->\s*([\d.]+)", line)
            if m:
                changes.append(Change(m.group(1), m.group(2), m.group(3)))
    if not changes and "would change" in (proc.stdout + proc.stderr).lower():
        changes.append(Change("(lockfile)", "current", "upgraded"))
    return changes


def _apply_uv_lock(ws: Workspace) -> None:
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")


def check_hatch(ws: Workspace) -> list[Change]:
    if (ws.path / "uv.lock").exists():
        return _check_uv_lock(ws)
    if shutil.which("uv"):
        venv_dir = ws.path / ".dep_refresh_venv"
        changes: list[Change] = []
        try:
            run(["uv", "venv", str(venv_dir)], ws.path, timeout=120)
            run(
                ["uv", "pip", "install", "-e", ".[dev]"],
                ws.path,
                timeout=300,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            proc = run(
                ["uv", "pip", "list", "--outdated"],
                ws.path,
                timeout=120,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            for line in proc.stdout.splitlines()[2:]:
                parts = line.split()
                if len(parts) >= 3:
                    changes.append(Change(parts[0], parts[1], parts[2]))
        finally:
            shutil.rmtree(venv_dir, ignore_errors=True)
        return changes
    print(f"WARN {ws.path}: hatch project without uv on PATH", file=sys.stderr)
    return []


def apply_hatch(ws: Workspace) -> None:
    if not (ws.path / "uv.lock").exists():
        proc = run(["uv", "lock"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "uv lock failed")
    _apply_uv_lock(ws)
