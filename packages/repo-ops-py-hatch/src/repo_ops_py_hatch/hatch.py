"""hatch upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY, TIMEOUT_CHECK

_EXTRAS_PREFERENCE = ("dev", "test", "all", "lint")


def _install_target(ws_path: Path) -> str:
    """Return the best ``-e <target>`` string for *ws_path*.

    Reads ``pyproject.toml`` and picks the first optional-dependencies group
    from the preference list (dev → test → all → lint).  Falls back to plain
    ``"."`` when none of those groups exist or the file is absent/malformed.
    """
    data = read_toml_file(ws_path / "pyproject.toml") or {}
    extras: dict[str, object] = (data.get("project") or {}).get(
        "optional-dependencies"
    ) or {}
    for group in _EXTRAS_PREFERENCE:
        if group in extras:
            return f".[{group}]"
    return "."


def _check_uv_lock(ws: Workspace) -> list[Change]:
    proc = run(["uv", "lock", "--upgrade", "--dry-run"], ws.path, timeout=TIMEOUT_CHECK)
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
        if not shutil.which("uv"):
            print(f"WARN {ws.path}: hatch project without uv on PATH", file=sys.stderr)
            return []
        return _check_uv_lock(ws)
    if shutil.which("uv"):
        changes: list[Change] = []
        with tempfile.TemporaryDirectory(prefix="repo-ops-hatch-") as tmp:
            venv_dir = Path(tmp) / "venv"
            venv_proc = run(["uv", "venv", str(venv_dir)], ws.path, timeout=120)
            if venv_proc.returncode != 0:
                raise RuntimeError(venv_proc.stderr.strip() or "uv venv failed")
            install_target = _install_target(ws.path)
            install_proc = run(
                ["uv", "pip", "install", "-e", install_target],
                ws.path,
                timeout=300,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            if install_proc.returncode != 0:
                raise RuntimeError(
                    install_proc.stderr.strip() or "uv pip install failed"
                )
            proc = run(
                ["uv", "pip", "list", "--outdated"],
                ws.path,
                timeout=120,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    proc.stderr.strip() or "uv pip list --outdated failed"
                )
            for line in proc.stdout.splitlines()[2:]:
                parts = line.split()
                if len(parts) >= 3:
                    changes.append(Change(parts[0], parts[1], parts[2]))
        return changes
    print(f"WARN {ws.path}: hatch project without uv on PATH", file=sys.stderr)
    return []


def apply_hatch(ws: Workspace) -> None:
    if not shutil.which("uv"):
        print(f"WARN {ws.path}: hatch project without uv on PATH", file=sys.stderr)
        return
    if not (ws.path / "uv.lock").exists():
        proc = run(["uv", "lock"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "uv lock failed")
    _apply_uv_lock(ws)
