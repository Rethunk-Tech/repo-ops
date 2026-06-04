"""pip-tools upgrade check/apply."""

from __future__ import annotations

import shutil
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_py.requirements import parse_requirements_lines
from repo_ops_upgrade.constants import TIMEOUT_APPLY, TIMEOUT_CHECK


def check_pip_tools(ws: Workspace) -> list[Change]:
    req_in = ws.path / "requirements.in"
    if not req_in.exists():
        return []
    req_txt = ws.path / "requirements.txt"
    current: dict[str, str] = {}
    if req_txt.exists():
        current = parse_requirements_lines(
            req_txt.read_text(encoding="utf-8", errors="replace")
        )
    proposed: dict[str, str] = {}
    if shutil.which("uv"):
        proc = run(
            ["uv", "pip", "compile", str(req_in), "-o", "-", "--upgrade", "--quiet"],
            ws.path,
            timeout=TIMEOUT_CHECK,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            proposed = parse_requirements_lines(proc.stdout)
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
            proposed = parse_requirements_lines(proc.stdout)
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


def _compile_requirements(ws: Workspace, out: Path, req_in: Path) -> None:
    if shutil.which("uv"):
        proc = run(
            ["uv", "pip", "compile", str(req_in), "-o", str(out), "--upgrade"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        if proc.returncode == 0:
            return
    if not shutil.which("pip-compile"):
        raise RuntimeError("pip-compile not on PATH and uv pip compile failed")
    proc = run(
        ["pip-compile", "--upgrade", "-o", str(out), str(req_in)],
        ws.path,
        timeout=TIMEOUT_APPLY,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip-compile failed")


def apply_pip_tools(ws: Workspace) -> None:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    out = req_txt if req_txt.exists() else req_in.with_suffix(".txt")
    if not req_in.exists():
        raise RuntimeError("requirements.in not found")
    _compile_requirements(ws, out, req_in)
