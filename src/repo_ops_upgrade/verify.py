"""Post-apply verification gates per ecosystem.

Per-repo verify profiles
------------------------
The ``[verify]`` table in ``.repo-ops.toml`` (or the global config) supports
three knobs:

``verify.script`` (str)
    Overrides gate delegation with a specific package.json script name.  Empty
    string skips the script step.  Real-world use: Heroku repos that need
    ``yarn install --immutable && yarn build:production`` as their gate can
    declare ``script = "build:production"`` instead of relying on gate.

``verify.skip`` (bool)
    Skip the verify gate entirely for this repo. Returns ``None`` (same as
    a successful gate) after logging ``verify skipped (config)``.

``verify.timeout`` (int, seconds)
    Per-repo override for the verify timeout.

Precedence: workspace config > repo config > global config > default.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from repo_ops_core.config import (
    load_global_config,
    load_repo_config,
)
from repo_ops_core.constants import GO_MODULE_MODE_ENV, TIMEOUT_VERIFY
from repo_ops_core.process import run
from repo_ops_js.workspaces import pm_ignore_scripts_env, pm_run_prefix, with_ignore_scripts
from repo_ops_models import OwnedEdit, Workspace
from repo_ops_py.requirements import parse_requirements_lines
from repo_ops_py_pip_tools.pip_tools import uv_compile_command
from repo_ops_upgrade.constants import YARN_PEER_WARN

logger = logging.getLogger(__name__)


def _gate_failure_detail(proc: Any, names: list[str]) -> str:
    """Failure text starting at the first ``gate: FAIL`` line.

    gate writes progress lines (``gate: install: ...``) to stderr before any
    step runs; leading with stderr names a step that passed. The FAIL line and
    the ``full log`` path that follows it name the step that did not.
    """
    lines = (proc.stdout + "\n" + proc.stderr).splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("gate: FAIL")), None)
    if start is not None:
        tail = [ln for ln in lines[start:] if not ln.startswith("gate: install:")]
        return "\n".join(tail).strip()
    return (proc.stderr or proc.stdout).strip() or f"gate run {' '.join(names)} failed"


def _timeout_failure(timeout: int, source: str) -> str:
    """Name where the verify timeout came from and how to raise it."""
    if source == "[verify] timeout in .repo-ops.toml":
        how = "increase [verify] timeout in .repo-ops.toml"
    elif source == ".gate.toml":
        how = "increase timeout in .gate.toml"
    else:
        # Verify passes --timeout 0 to gate, so a hang dies at this process
        # limit, not a per-gate timeout in .gate.toml.
        how = "set [verify] timeout in .repo-ops.toml"
    return f"timed out after {timeout}s ({source}; {how})"


def _timed_run(
    cmd: list[str],
    cwd: Path,
    *,
    timeout: int,
    source: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(cmd, cwd, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        n = int(exc.timeout) if exc.timeout is not None else timeout
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=_timeout_failure(n, source))


def _gate_verify(ws: Workspace, *, timeout: int, source: str = "default") -> str | None:
    """Verify *ws* by running the gates the project itself declares.

    ``gate --json`` executes nothing and exits 0 even when it detects nothing,
    so it is both the availability probe and the gate set. Every detected gate
    runs, tests included: a partial gate reporting green is the failure mode
    verify exists to catch. A missing ``gate`` CLI, or a project it detects
    nothing in, is a verify failure, not a cue to guess a command.
    """
    if not shutil.which("gate"):
        return f"gate not on PATH; verify of {ws.path} needs the gate CLI"
    # JSON, not the --list text: a multi-line inline gate script prints lines
    # indented like gate names, and scraping them ran ``gate run git``.
    listing = _timed_run(["gate", "--json"], ws.path, timeout=timeout, source=source)
    if listing.returncode != 0 and "timed out after" in listing.stderr:
        return listing.stderr.strip()
    try:
        # Opt-in e2e gates are listed but skipped by a default gate run; running
        # them by name here is what made verify outlast its timeout.
        names = [
            g["name"] for g in json.loads(listing.stdout).get("gates") or [] if not g.get("skipped")
        ]
    except (ValueError, AttributeError, TypeError, KeyError):
        names = []
    if listing.returncode != 0 or not names:
        return f"gate detects no gate for {ws.path}"
    # gate's own per-gate timeout would race this process's; 0 disables it and
    # leaves the caller's verify timeout the single authority.
    proc = _timed_run(
        ["gate", "--timeout", "0", "run", *names], ws.path, timeout=timeout, source=source
    )
    if proc.returncode != 0:
        if "timed out after" in proc.stderr:
            return proc.stderr.strip()
        return _gate_failure_detail(proc, names)
    return None


def _load_verify_profile(ws: Workspace) -> dict[str, Any]:
    """Return the merged ``[verify]`` sub-table for *ws*.

    Layers, lowest first: global config, the repo root's config, then the
    workspace directory's own config when it sits below the repo root. The last
    layer lets one workspace opt out (a self-test fixture that is broken on
    purpose) without skipping verify for its siblings.
    Keys: ``script`` (str), ``skip`` (bool), ``timeout`` (int seconds).
    """
    layers = [load_global_config(), load_repo_config(ws.git_repo)]
    if ws.path != ws.git_repo:
        layers.append(load_repo_config(ws.path))
    profile: dict[str, Any] = {}
    for cfg in layers:
        if cfg:
            profile.update(cfg.get("verify", {}))
    return profile


def _resolve_verify_timeout_with_profile(profile: dict[str, Any]) -> tuple[int, str]:
    """Resolve the verify timeout and a label for where that value came from."""
    cfg_timeout = profile.get("timeout")
    if isinstance(cfg_timeout, int) and cfg_timeout > 0:
        return cfg_timeout, "[verify] timeout in .repo-ops.toml"
    return TIMEOUT_VERIFY, "default"


def verify_node(ws: Workspace) -> str | None:
    """Run immutable install then the configured verify script for a Node workspace.

    Script resolution order (first match wins):

    1. ``verify.script`` in per-repo / global config (empty → skip script step)
    2. The gates the project declares, run through the ``gate`` CLI

    Step 2 fails verify when ``gate`` is absent or detects nothing; no script is guessed.
    """
    profile = _load_verify_profile(ws)
    mgr = ws.manager
    timeout, source = _resolve_verify_timeout_with_profile(profile)

    lock_cmds = {
        "yarn": ["yarn", "install", "--immutable"],
        "npm": ["npm", "ci"],
        "bun": ["bun", "install", "--frozen-lockfile"],
        "pnpm": ["pnpm", "install", "--frozen-lockfile"],
    }
    lock = lock_cmds.get(mgr)
    if lock:
        lock = with_ignore_scripts(lock)
        proc = _timed_run(
            lock,
            ws.path,
            timeout=timeout,
            source=source,
            env=pm_ignore_scripts_env(lock) or None,
        )
        combined = proc.stdout + proc.stderr
        if proc.returncode != 0 or (mgr == "yarn" and YARN_PEER_WARN.search(combined)):
            return combined.strip() or f"{mgr} immutable install failed"

    cfg_script = profile.get("script")
    if cfg_script is None:
        return _gate_verify(ws, timeout=timeout, source=source)
    script_name = str(cfg_script).strip()
    # Explicit empty string in config → skip script step.
    if not script_name:
        return None

    proc = _timed_run([*pm_run_prefix(mgr), script_name], ws.path, timeout=timeout, source=source)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or f"{script_name} failed"
    return None


def verify_workspace(
    ws: Workspace,
    *,
    owned_edits: dict[str, OwnedEdit] | None = None,
) -> str | None:
    """Run the appropriate verify gate for *ws*.

    Respects per-repo ``[verify]`` config keys:
    - ``skip = true`` → returns ``None`` immediately (gate treated as passed)
      after logging ``verify skipped (config)``.
    - ``timeout`` → used as the verify timeout.
    - ``script`` → forwarded to the Node path via :func:`verify_node`.

    Returns ``None`` on success (or intentional skip); a non-empty string is
    the verify failure detail.

    *owned_edits* is the apply's own diff. Container verify digest-checks
    only image refs on rewritten Dockerfile/compose lines in that diff.
    """
    profile = _load_verify_profile(ws)

    if profile.get("skip"):
        logger.info("verify skipped (config) for %s", ws.path)
        return None

    mgr = ws.manager
    timeout, source = _resolve_verify_timeout_with_profile(profile)
    if mgr in ("yarn", "npm", "bun", "pnpm"):
        return verify_node(ws)
    if mgr == "go":
        proc = _timed_run(
            ["go", "mod", "verify"],
            ws.path,
            timeout=timeout,
            source=source,
            env=GO_MODULE_MODE_ENV,
        )
        if proc.returncode != 0:
            return proc.stderr.strip() or "go mod verify failed"
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr in ("rust", "nuget"):
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr in ("uv", "hatch"):
        # Hatch without uv.lock: apply is a no-op unless migrate-to-uv-lock;
        # cannot run uv sync --locked. Only the declared gates run.
        if mgr == "hatch" and not (ws.path / "uv.lock").is_file():
            logger.info(
                "hatch verify: no uv.lock at %s; skipping uv sync --locked",
                ws.path,
            )
            return _gate_verify(ws, timeout=timeout, source=source)
        if not shutil.which("uv"):
            return "uv not on PATH"
        # gate does not check that a lockfile is frozen-installable, so this
        # runs whether or not the declared gates take over below.
        proc = _timed_run(["uv", "sync", "--locked"], ws.path, timeout=timeout, source=source)
        if proc.returncode != 0:
            return proc.stderr.strip() or "uv sync --locked failed"
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr == "poetry":
        if not shutil.which("poetry"):
            return "poetry not on PATH"
        proc = _timed_run(["poetry", "check"], ws.path, timeout=timeout, source=source)
        if proc.returncode != 0:
            return proc.stderr.strip() or "poetry check failed"
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr == "pdm":
        if not shutil.which("pdm"):
            return "pdm not on PATH"
        proc = _timed_run(["pdm", "sync", "--clean"], ws.path, timeout=timeout, source=source)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "pdm sync --clean failed"
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr == "pipenv":
        if not shutil.which("pipenv"):
            return "pipenv not on PATH"
        proc = _timed_run(["pipenv", "verify"], ws.path, timeout=timeout, source=source)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "pipenv verify failed"
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr == "pip-tools":
        return _verify_pip_tools(ws, timeout=timeout, source=source)
    if mgr == "deno":
        if not shutil.which("deno"):
            return "deno not on PATH"
        # Frozen install from deno.lock when present; otherwise type-check.
        if (ws.path / "deno.lock").is_file():
            proc = _timed_run(
                ["deno", "install", "--frozen"],
                ws.path,
                timeout=timeout,
                source=source,
            )
            if proc.returncode != 0:
                return (proc.stderr or proc.stdout).strip() or "deno install --frozen failed"
            return _gate_verify(ws, timeout=timeout, source=source)
        proc = _timed_run(["deno", "check", "."], ws.path, timeout=timeout, source=source)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "deno check failed"
        return _gate_verify(ws, timeout=timeout, source=source)
    if mgr == "precommit":
        if not shutil.which("pre-commit"):
            return "pre-commit not on PATH"
        proc = _timed_run(
            ["pre-commit", "validate-config"],
            ws.path,
            timeout=timeout,
            source=source,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "pre-commit validate-config failed"
        return None
    if mgr == "container":
        from repo_ops_container.container import verify_container

        return verify_container(ws, owned_edits or {})
    return None


def _verify_pip_tools(
    ws: Workspace, *, timeout: int | None = None, source: str = "default"
) -> str | None:
    """Verify pip-tools workspace by recompiling requirements.in and checking for drift."""
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    if not req_in.exists():
        return None
    if timeout is None:
        timeout = TIMEOUT_VERIFY
    if shutil.which("uv"):
        proc = _timed_run(
            [*uv_compile_command(req_txt, req_in, "-", upgrade=False), "--quiet"],
            ws.path,
            timeout=timeout,
            source=source,
        )
        # uv writes `-o -` to stdout and also to a file named `-` in cwd.
        (ws.path / "-").unlink(missing_ok=True)
        if proc.returncode != 0:
            return proc.stderr.strip() or "uv pip compile failed"
        compiled = proc.stdout
    elif shutil.which("pip-compile"):
        proc = _timed_run(
            ["pip-compile", "--quiet", "-o", "-", str(req_in)],
            ws.path,
            timeout=timeout,
            source=source,
        )
        if proc.returncode != 0:
            return proc.stderr.strip() or "pip-compile failed"
        compiled = proc.stdout
    else:
        return "uv not on PATH"
    if not req_txt.exists():
        return "requirements.txt missing after apply"
    current = parse_requirements_lines(req_txt.read_text(encoding="utf-8", errors="replace"))
    proposed = parse_requirements_lines(compiled)
    if not proposed:
        return None
    drift = {pkg for pkg in set(current) | set(proposed) if current.get(pkg) != proposed.get(pkg)}
    if drift:
        return f"requirements.txt is out of sync: {', '.join(sorted(drift))}"
    return None
