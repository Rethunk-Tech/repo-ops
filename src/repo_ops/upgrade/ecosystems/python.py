"""Python ecosystem upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops.core.process import run
from repo_ops.upgrade.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops.upgrade.models import Change, Workspace

def _parse_requirements_lines(text: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*==\s*(\S+)", line)
        if m:
            versions[m.group(1).lower().replace("_", "-")] = m.group(2)
    return versions

def check_uv(ws: Workspace) -> list[Change]:
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

def check_poetry(ws: Workspace) -> list[Change]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return []
    proc = run(["poetry", "update", "--dry-run"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update --dry-run failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = re.search(r"Updating\s+(\S+)\s+\(([^)]+)\s+->\s+([^)]+)\)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes

def check_pip_tools(ws: Workspace) -> list[Change]:
    req_in = ws.path / "requirements.in"
    if not req_in.exists():
        return []
    req_txt = ws.path / "requirements.txt"
    current: dict[str, str] = {}
    if req_txt.exists():
        current = _parse_requirements_lines(
            req_txt.read_text(encoding="utf-8", errors="replace")
        )
    proposed: dict[str, str] = {}
    if shutil.which("uv"):
        proc = run(
            ["uv", "pip", "compile", str(req_in), "-o", "-", "--quiet"],
            ws.path,
            timeout=TIMEOUT_CHECK,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            proposed = _parse_requirements_lines(proc.stdout)
    if not proposed and shutil.which("pip-compile"):
        proc = run(
            ["pip-compile", "--dry-run", "--quiet", "-o", "-", str(req_in)],
            ws.path,
            timeout=TIMEOUT_CHECK,
        )
        if proc.returncode != 0:
            proc = run(
                ["pip-compile", "--upgrade", "--quiet", "-o", "-", str(req_in)],
                ws.path,
                timeout=TIMEOUT_CHECK,
            )
        if proc.returncode == 0 and proc.stdout.strip():
            proposed = _parse_requirements_lines(proc.stdout)
        elif proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "pip-compile failed")
    if not proposed:
        return []
    changes: list[Change] = []
    for pkg in sorted(set(current) | set(proposed)):
        cur = current.get(pkg)
        prop = proposed.get(pkg)
        if cur != prop and prop:
            changes.append(Change(pkg, cur or "?", prop))
    return changes

def check_hatch(ws: Workspace) -> list[Change]:
    if (ws.path / "uv.lock").exists():
        return check_uv(ws)
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

def apply_uv(ws: Workspace) -> None:
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")

def apply_poetry(ws: Workspace) -> None:
    proc = run(["poetry", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")

def apply_pip_tools(ws: Workspace) -> None:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    out = req_txt if req_txt.exists() else req_in.with_suffix(".txt")
    args = ["pip-compile", "--upgrade"]
    if req_in.exists():
        args.extend(["-o", str(out), str(req_in)])
    proc = run(args, ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip-compile failed")

def apply_hatch(ws: Workspace) -> None:
    if not (ws.path / "uv.lock").exists():
        proc = run(["uv", "lock"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "uv lock failed")
    apply_uv(ws)
