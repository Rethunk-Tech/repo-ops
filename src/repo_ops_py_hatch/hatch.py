"""hatch upgrade check/apply."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_py.uv_lock import apply_uv_lock, check_uv_lock

_EXTRAS_PREFERENCE = ("dev", "test", "all", "lint")


def _install_target(ws_path: Path) -> str:
    """Return the best ``-e <target>`` string for *ws_path*.

    Reads ``pyproject.toml`` and picks the first optional-dependencies group
    from the preference list (dev → test → all → lint).  Falls back to plain
    ``"."`` when none of those groups exist or the file is absent/malformed.
    """
    data = read_toml_file(ws_path / "pyproject.toml") or {}
    extras: dict[str, object] = (data.get("project") or {}).get("optional-dependencies") or {}
    for group in _EXTRAS_PREFERENCE:
        if group in extras:
            return f".[{group}]"
    return "."


def check_hatch(ws: Workspace) -> list[Change]:
    if (ws.path / "uv.lock").exists():
        if not shutil.which("uv"):
            print(f"WARN {ws.path}: hatch project without uv on PATH", file=sys.stderr)
            return []
        return check_uv_lock(ws)
    # No uv.lock yet: apply_hatch creates one, so probe for drift as if it existed.
    if shutil.which("uv"):
        changes: list[Change] = []
        with tempfile.TemporaryDirectory(prefix="repo-ops-hatch-") as tmp:
            venv_dir = Path(tmp) / "venv"
            venv_proc = run(
                ["uv", "venv", str(venv_dir)],
                ws.path,
                timeout=TIMEOUT_CHECK,
            )
            if venv_proc.returncode != 0:
                raise RuntimeError(venv_proc.stderr.strip() or "uv venv failed")
            install_target = _install_target(ws.path)
            install_proc = run(
                ["uv", "pip", "install", "-e", install_target],
                ws.path,
                timeout=TIMEOUT_CHECK,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            if install_proc.returncode != 0:
                raise RuntimeError(install_proc.stderr.strip() or "uv pip install failed")
            proc = run(
                ["uv", "pip", "list", "--outdated"],
                ws.path,
                timeout=TIMEOUT_CHECK,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "uv pip list --outdated failed")
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
    apply_uv_lock(ws)
