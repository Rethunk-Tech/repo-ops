"""pip-tools upgrade check/apply."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from functools import cache
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import quote

from repo_ops_core.config import resolve_bool_setting
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.process import run
from repo_ops_core.requirement import requirement_satisfied
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
_PIP_TOOLS_TARGETED_BUMP_ENV = "REPO_OPS_PIP_TOOLS_TARGETED_BUMP"


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


def _requirements_in_constraints(req_in: Path) -> dict[str, str]:
    if not req_in.exists():
        return {}
    constraints: dict[str, str] = {}
    for raw_line in req_in.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = _REQ_NAME_RE.match(line)
        if match is None:
            continue
        rest = line[match.end() :]
        if "#" in rest:
            rest = rest.split("#", 1)[0]
        rest = rest.strip()
        if rest.startswith("["):
            closing = rest.find("]")
            rest = rest[closing + 1 :].strip() if closing >= 0 else ""
        constraints[match.group(1)] = rest.split(";", 1)[0].strip()
    return constraints


def _pip_tools_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    return resolve_bool_setting(
        "pip_tools_targeted_bump", _PIP_TOOLS_TARGETED_BUMP_ENV, ws
    )


def _pip_tools_version_parts(version: str) -> tuple[int, int, int]:
    match = re.search(r"\d+(?:\.\d+){0,2}", version.strip().lstrip("vV"))
    if match is None:
        return (0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")]
    padded = [*parts, 0, 0, 0][:3]
    return padded[0], padded[1], padded[2]


def _pip_tools_requirement_satisfied(version: str, requirement: str) -> bool:
    return requirement_satisfied(version, requirement, default_operator="==")


def _pypi_latest(package: str) -> str | None:
    connection = HTTPSConnection("pypi.org", timeout=resolve_timeout_check())
    try:
        connection.request(
            "GET",
            f"/pypi/{quote(package, safe='')}/json",
            headers={"User-Agent": "repo-ops"},
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            return None
        document = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        connection.close()
    if not isinstance(document, dict):
        return None
    info = document.get("info")
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        return info["version"]
    return None


def _pip_tools_latest_direct_changes(ws: Workspace) -> list[Change]:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    current = (
        parse_requirements_lines(req_txt.read_text(encoding="utf-8", errors="replace"))
        if req_txt.exists()
        else {}
    )
    changes: list[Change] = []
    for name, requirement in sorted(_requirements_in_constraints(req_in).items()):
        locked = current.get(name)
        latest = _pypi_latest(name)
        if (
            locked is None
            or latest is None
            or _pip_tools_version_parts(latest) <= _pip_tools_version_parts(locked)
            or _pip_tools_requirement_satisfied(latest, requirement)
        ):
            continue
        changes.append(Change(name, locked, latest))
    return changes


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


def check_pip_tools(
    ws: Workspace,
) -> list[Change] | tuple[list[Change], list[Change]]:
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
        if _pip_tools_targeted_bump_enabled(ws):
            return [], _pip_tools_latest_direct_changes(ws)
        return []
    changes: list[Change] = []
    for pkg in sorted(set(current) | set(proposed)):
        cur = current.get(pkg)
        prop = proposed.get(pkg)
        if cur != prop and prop:
            changes.append(Change(pkg, cur or "?", prop))
    if not _pip_tools_targeted_bump_enabled(ws):
        return changes
    return changes, _pip_tools_latest_direct_changes(ws)


def _compile_requirements(
    ws: Workspace,
    out: Path,
    req_in: Path,
    constraints_path: Path | None = None,
    upgrade_package: str | None = None,
) -> None:
    if shutil.which("uv"):
        proc = run(
            _with_constraints(
                [
                    "uv",
                    "pip",
                    "compile",
                    str(req_in),
                    "-o",
                    str(out),
                    "--upgrade",
                    *(
                        ["--upgrade-package", upgrade_package]
                        if upgrade_package is not None
                        else []
                    ),
                ],
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
            [
                "pip-compile",
                "--upgrade",
                *(
                    ["--upgrade-package", upgrade_package]
                    if upgrade_package is not None
                    else []
                ),
                "-o",
                str(out),
                str(req_in),
            ],
            constraints_path,
        ),
        ws.path,
        timeout=resolve_timeout_apply(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip-compile failed")


def _pip_tools_bumped_requirement(requirement: str, latest: str) -> str:
    stripped = requirement.strip()
    for operator in ("^", "~=", "~", "==", ">=", "<=", "!=", ">", "<", "="):
        if stripped.startswith(operator):
            return f"{operator}{latest}"
    return f"=={latest}"


def _rewrite_requirements_in(req_in: Path, name: str, latest: str) -> bool:
    original = req_in.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines(keepends=True)
    rewritten = False
    output: list[str] = []
    for line in lines:
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+)(.*)$", line.rstrip("\r\n"))
        if match is None or match.group(2) != name:
            output.append(line)
            continue
        suffix = match.group(3)
        newline = line[len(line.rstrip("\r\n")) :]
        comment = ""
        if "#" in suffix:
            suffix, comment = suffix.split("#", 1)
            comment = f"#{comment}"
        marker = ""
        if ";" in suffix:
            suffix, marker = suffix.split(";", 1)
            marker = f";{marker}"
        extras = ""
        constraint = suffix.strip()
        if constraint.startswith("["):
            closing = constraint.find("]")
            if closing >= 0:
                extras = constraint[: closing + 1]
                constraint = constraint[closing + 1 :].strip()
        bumped = _pip_tools_bumped_requirement(constraint, latest)
        output.append(
            f"{match.group(1)}{name}{extras}{bumped}{marker}{comment}{newline}"
        )
        rewritten = True
    if rewritten:
        req_in.write_text("".join(output), encoding="utf-8")
    return rewritten


def _pip_tools_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _pip_tools_restore(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(snapshot)


def _apply_pip_tools_targeted_bumps(
    ws: Workspace,
    candidates: list[Change],
) -> list[Change]:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    remaining: list[Change] = []
    for candidate in candidates:
        in_snapshot = _pip_tools_snapshot(req_in)
        txt_snapshot = _pip_tools_snapshot(req_txt)
        if not _rewrite_requirements_in(req_in, candidate.name, candidate.to):
            remaining.append(candidate)
            continue
        out = req_txt if req_txt.exists() else req_in.with_suffix(".txt")
        try:
            with _hold_constraints_file(ws, req_in) as constraints_path:
                _compile_requirements(
                    ws,
                    out,
                    req_in,
                    constraints_path,
                    upgrade_package=f"{candidate.name}=={candidate.to}",
                )
        except Exception:
            _pip_tools_restore(req_in, in_snapshot)
            _pip_tools_restore(req_txt, txt_snapshot)
            raise
        landed = parse_requirements_lines(
            out.read_text(encoding="utf-8", errors="replace")
        ).get(candidate.name)
        if landed == candidate.to:
            continue
        _pip_tools_restore(req_in, in_snapshot)
        _pip_tools_restore(req_txt, txt_snapshot)
        remaining.append(candidate)
    return remaining


def apply_pip_tools(ws: Workspace) -> list[Change] | None:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    out = req_txt if req_txt.exists() else req_in.with_suffix(".txt")
    if not req_in.exists():
        raise RuntimeError("requirements.in not found")
    with _hold_constraints_file(ws, req_in) as constraints_path:
        _compile_requirements(ws, out, req_in, constraints_path)
    if not _pip_tools_targeted_bump_enabled(ws):
        return None
    return _apply_pip_tools_targeted_bumps(
        ws,
        _pip_tools_latest_direct_changes(ws),
    )
