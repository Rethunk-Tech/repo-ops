"""pip-tools upgrade check/apply."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from functools import cache
from pathlib import Path

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.version_utils import hold_ceiling_spec
from repo_ops_models import Change, Workspace
from repo_ops_py.requirements import parse_requirements_lines

# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------
#
# pip-tools compiles requirements.txt from requirements.in (a source
# manifest, not an installed tree) — there's no per-package "install this
# exact version" primitive to clamp down after the fact. Instead, a hold is
# enforced by passing an ephemeral pip constraints file (``-c``) to the
# compile step with a ``name<=ceiling`` line for each held direct dependency,
# so the compiled output never proposes crossing the ceiling in the first
# place. The constraints file is temporary and never written into
# requirements.in itself — config/manifest files stay untouched.

_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")


@cache
def _global_holds_cached() -> tuple[HoldRule, ...]:
    return tuple(load_global_holds())


@cache
def _repo_holds_cached(repo_root: str) -> tuple[HoldRule, ...]:
    return tuple(load_repo_holds(Path(repo_root)))


def _workspace_rel(ws: Workspace) -> str:
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve()).as_posix()
    except ValueError:
        return "."
    return rel or "."


def _resolve_pkg_hold(ws: Workspace, package: str) -> HoldRule | None:
    repo_root = str(ws.git_repo.resolve())
    rules = [*_global_holds_cached(), *_repo_holds_cached(repo_root)]
    if not rules:
        return None
    return resolve_hold(
        rules,
        package=package,
        ecosystem=ws.ecosystem,
        manager=ws.manager,
        workspace_rel=_workspace_rel(ws),
    )


def _requirements_in_names(req_in: Path) -> set[str]:
    """Direct dependency names from requirements.in (name only, spec ignored).

    ``parse_requirements_lines`` only matches exact ``==`` pins (the compiled
    requirements.txt format) and returns nothing useful for a loose-spec
    source file like requirements.in, so this is a dedicated name-only parser.
    """
    if not req_in.exists():
        return set()
    names: set[str] = set()
    for line in req_in.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_NAME_RE.match(line)
        if m:
            names.add(m.group(1))
    return names


@contextmanager
def _hold_constraints_file(ws: Workspace, req_in: Path):
    """Yield a temp pip constraints-file Path for held direct deps, or None.

    Deleted on exit regardless of outcome — requirements.in/.txt are never
    touched.
    """
    lines: list[str] = []
    for name in sorted(_requirements_in_names(req_in)):
        rule = _resolve_pkg_hold(ws, name)
        if rule is not None:
            lines.append(f"{name}{hold_ceiling_spec(rule.max_version)}")
    if not lines:
        yield None
        return
    fd, raw_path = tempfile.mkstemp(
        prefix="repo-ops-hold-", suffix=".txt", dir=str(ws.path)
    )
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def _with_constraints(cmd: list[str], constraints_path: Path | None) -> list[str]:
    if constraints_path is None:
        return cmd
    return [*cmd, "-c", str(constraints_path)]


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
    uv_failed = False
    with _hold_constraints_file(ws, req_in) as constraints_path:
        if shutil.which("uv"):
            proc = run(
                _with_constraints(
                    [
                        "uv",
                        "pip",
                        "compile",
                        str(req_in),
                        "-o",
                        "-",
                        "--upgrade",
                        "--quiet",
                    ],
                    constraints_path,
                ),
                ws.path,
                timeout=resolve_timeout_check(),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                proposed = parse_requirements_lines(proc.stdout)
            elif proc.returncode != 0:
                uv_failed = True
                print(
                    f"WARN uv pip compile failed: {proc.stderr.strip()}",
                    file=sys.stderr,
                )
        if not proposed and shutil.which("pip-compile"):
            proc = run(
                _with_constraints(
                    [
                        "pip-compile",
                        "--upgrade",
                        "--dry-run",
                        "--quiet",
                        "-o",
                        "-",
                        str(req_in),
                    ],
                    constraints_path,
                ),
                ws.path,
                timeout=resolve_timeout_check(),
            )
            if proc.returncode != 0:
                proc = run(
                    _with_constraints(
                        ["pip-compile", "--upgrade", "--quiet", "-o", "-", str(req_in)],
                        constraints_path,
                    ),
                    ws.path,
                    timeout=resolve_timeout_check(),
                )
            if proc.returncode == 0 and proc.stdout.strip():
                proposed = parse_requirements_lines(proc.stdout)
            elif proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "pip-compile failed")
    if not proposed:
        if uv_failed and not shutil.which("pip-compile"):
            raise RuntimeError("pip-compile not on PATH and uv pip compile failed")
        return []
    changes: list[Change] = []
    for pkg in sorted(set(current) | set(proposed)):
        cur = current.get(pkg)
        prop = proposed.get(pkg)
        if cur != prop and prop:
            changes.append(Change(pkg, cur or "?", prop))
    return changes


def _compile_requirements(
    ws: Workspace, out: Path, req_in: Path, constraints_path: Path | None = None
) -> None:
    if shutil.which("uv"):
        proc = run(
            _with_constraints(
                ["uv", "pip", "compile", str(req_in), "-o", str(out), "--upgrade"],
                constraints_path,
            ),
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        if proc.returncode == 0:
            return
        print(f"WARN uv pip compile failed: {proc.stderr.strip()}", file=sys.stderr)
    if not shutil.which("pip-compile"):
        raise RuntimeError("pip-compile not on PATH and uv pip compile failed")
    proc = run(
        _with_constraints(
            ["pip-compile", "--upgrade", "-o", str(out), str(req_in)],
            constraints_path,
        ),
        ws.path,
        timeout=resolve_timeout_apply(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip-compile failed")


def apply_pip_tools(ws: Workspace) -> None:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    out = req_txt if req_txt.exists() else req_in.with_suffix(".txt")
    if not req_in.exists():
        raise RuntimeError("requirements.in not found")
    with _hold_constraints_file(ws, req_in) as constraints_path:
        _compile_requirements(ws, out, req_in, constraints_path)
