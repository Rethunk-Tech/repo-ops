"""Post-apply verification gates per ecosystem.

Per-repo verify profiles
------------------------
Non-Python repos carry no pyproject.toml, so the layered config module also
reads a ``.repo-ops.toml`` sidecar at the repo root.  The ``[verify]`` table
(or flat ``verify.*`` keys in the sidecar) supports three knobs:

``verify.script`` (str)
    Overrides the Node.js waterfall (ci → ci:pushgate → build → lint) with a
    specific package.json script name.  Empty string skips the script step
    (same as ``REPO_OPS_NODE_VERIFY_SCRIPT=""``).  Real-world use: Heroku
    repos that need ``yarn install --immutable && yarn build:production`` as
    their gate can declare ``script = "build:production"`` instead of
    relying on a ``ci`` script.

``verify.skip`` (bool)
    Skip the verify gate entirely for this repo; reported as
    ``"verify skipped (config)"``.

``verify.timeout`` (int, seconds)
    Per-repo override for the verify timeout.  Supersedes the global
    ``REPO_OPS_TIMEOUT_VERIFY`` env var only when the env var is absent.

Precedence: env var > per-repo config > global config > default.
The env var always wins for one-shot overrides (CI pipelines).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from typing import Any

from repo_ops_core.config import load_global_config, load_repo_config
from repo_ops_core.constants import resolve_timeout_verify
from repo_ops_core.filesystem import read_json_file, read_toml_file
from repo_ops_core.process import run
from repo_ops_js.workspaces import pm_run_prefix
from repo_ops_models import Workspace

from repo_ops_upgrade.constants import YARN_PEER_WARN

logger = logging.getLogger(__name__)

_DEP_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")


def _dep_name(spec: str) -> str:
    """Extract the bare package name from a PEP 508 dependency specifier."""
    m = _DEP_NAME_RE.match(spec.strip())
    return m.group(1).lower().replace("_", "-") if m else spec.strip().lower()


def _pyproject_dev_deps(d: dict) -> set[str]:
    dev: set[str] = set()
    opt = (d.get("project") or {}).get("optional-dependencies") or {}
    for vals in opt.values():
        if isinstance(vals, list):
            for spec in vals:
                if isinstance(spec, str):
                    dev.add(_dep_name(spec))
    tool = d.get("tool") or {}
    # Hatch envs
    hatch_envs = (tool.get("hatch") or {}).get("envs") or {}
    for env_cfg in hatch_envs.values():
        if isinstance(env_cfg, dict):
            for dep in env_cfg.get("dependencies") or []:
                if isinstance(dep, str):
                    dev.add(_dep_name(dep))
    # uv dev-dependencies
    uv_dev = (tool.get("uv") or {}).get("dev-dependencies") or []
    for spec in uv_dev:
        if isinstance(spec, str):
            dev.add(_dep_name(spec))
    return dev


def _verify_hatch_fast(ws: Workspace, *, timeout: int | None = None) -> str | None:
    d = read_toml_file(ws.path / "pyproject.toml")
    if not d:
        return None
    scripts = (d.get("project") or {}).get("scripts") or {}
    dev = _pyproject_dev_deps(d)
    if timeout is None:
        timeout = resolve_timeout_verify()
    # "test" script wins outright
    if "test" in scripts:
        proc = run(["uv", "run", "test"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run test failed"
        return None
    # pytest runner (explicit dep or tests/ dir)
    if "pytest" in dev or (ws.path / "tests").is_dir():
        proc = run(
            ["uv", "run", "pytest", "-q", "--co", "-q"], ws.path, timeout=timeout
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run pytest failed"
        return None
    # "check" or "typecheck" scripts (e.g. mypy / pyright wrappers)
    for script_key in ("check", "typecheck"):
        if script_key in scripts:
            proc = run(["uv", "run", script_key], ws.path, timeout=timeout)
            if proc.returncode != 0:
                return (
                    proc.stderr or proc.stdout
                ).strip() or f"uv run {script_key} failed"
            return None
    # mypy in dev deps
    if "mypy" in dev:
        proc = run(["uv", "run", "mypy", "."], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run mypy failed"
        return None
    # ruff in dev deps (lint-only fallback)
    if "ruff" in dev:
        proc = run(["uv", "run", "ruff", "check", "."], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run ruff check failed"
    return None


_NODE_VERIFY_SCRIPT_ENV = "REPO_OPS_NODE_VERIFY_SCRIPT"
_TIMEOUT_VERIFY_ENV = "REPO_OPS_TIMEOUT_VERIFY"
# Waterfall of default script candidates tried in order.
_NODE_VERIFY_CANDIDATES = ("ci", "ci:pushgate", "build", "lint")


def _load_verify_profile(ws: Workspace) -> dict[str, Any]:
    """Return the ``[verify]`` sub-table from per-repo config for *ws*.

    Looks up ``ws.git_repo`` (the repo root) via the layered config module and
    returns the ``verify`` sub-dict, merging global config as the base layer.
    Keys: ``script`` (str), ``skip`` (bool), ``timeout`` (int seconds).
    """
    global_cfg = load_global_config()
    repo_cfg = load_repo_config(ws.git_repo)
    # Per-repo [verify] wins over global [verify].
    global_verify: dict[str, Any] = global_cfg.get("verify", {}) if global_cfg else {}
    repo_verify: dict[str, Any] = repo_cfg.get("verify", {}) if repo_cfg else {}
    return {**global_verify, **repo_verify}


def _resolve_verify_timeout_with_profile(profile: dict[str, Any]) -> int:
    """Resolve verify timeout: env var > per-repo config > global config > default.

    *profile* is the merged ``[verify]`` dict from :func:`_load_verify_profile`.
    """
    # Env var always wins (one-shot override for CI pipelines).
    env_raw = os.environ.get(_TIMEOUT_VERIFY_ENV, "").strip()
    if env_raw:
        try:
            val = float(env_raw)
            if val > 0:
                return int(val)
        except ValueError:
            pass

    # Per-repo / global config.
    cfg_timeout = profile.get("timeout")
    if isinstance(cfg_timeout, int) and cfg_timeout > 0:
        return cfg_timeout

    return resolve_timeout_verify()


def verify_node(ws: Workspace) -> str | None:
    """Run immutable install then the configured verify script for a Node workspace.

    Script resolution order (first match wins):

    1. ``REPO_OPS_NODE_VERIFY_SCRIPT`` env var (empty → skip script step)
    2. ``verify.script`` in per-repo / global config (empty → skip script step)
    3. Waterfall: ci → ci:pushgate → build → lint (first found in package.json)
    4. Biome fallback (lint / biome:ci)
    """
    profile = _load_verify_profile(ws)
    mgr = ws.manager
    pkg = read_json_file(ws.path / "package.json") or {}
    scripts = pkg.get("scripts") or {}
    timeout = _resolve_verify_timeout_with_profile(profile)

    lock_cmds = {
        "yarn": ["yarn", "install", "--immutable"],
        "npm": ["npm", "ci"],
        "bun": ["bun", "install", "--frozen-lockfile"],
        "pnpm": ["pnpm", "install", "--frozen-lockfile"],
    }
    lock = lock_cmds.get(mgr)
    if lock:
        proc = run(lock, ws.path, timeout=timeout)
        combined = proc.stdout + proc.stderr
        if proc.returncode != 0 or (mgr == "yarn" and YARN_PEER_WARN.search(combined)):
            return combined.strip() or f"{mgr} immutable install failed"

    # Env override wins: explicit empty string → skip the script step entirely.
    env_val = os.environ.get(_NODE_VERIFY_SCRIPT_ENV)
    if env_val is not None:
        if env_val.strip() == "":
            return None
        script_name: str | None = env_val.strip()
    else:
        # Per-repo config script override (next in precedence after env var).
        cfg_script = profile.get("script")
        if cfg_script is not None:
            # Explicit empty string in config → skip script step.
            if str(cfg_script).strip() == "":
                return None
            script_name = str(cfg_script).strip()
        else:
            script_name = None
            for candidate in _NODE_VERIFY_CANDIDATES:
                if candidate in scripts:
                    script_name = candidate
                    break
            if not script_name and (
                (ws.path / "biome.json").exists() or (ws.path / "biome.jsonc").exists()
            ):
                script_name = "lint" if "lint" in scripts else None
                if not script_name:
                    for candidate in ("biome:ci",):
                        if candidate in scripts:
                            script_name = candidate
                            break

    if not script_name:
        return None

    proc = run([*pm_run_prefix(mgr), script_name], ws.path, timeout=timeout)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or f"{script_name} failed"
    return None


def _has_ruff_config(ws: Workspace, d: dict) -> bool:
    """Return True if ruff is configured in pyproject.toml or present in dev deps."""
    tool = d.get("tool") or {}
    if "ruff" in tool:
        return True
    dev = _pyproject_dev_deps(d)
    return "ruff" in dev


def verify_workspace(ws: Workspace, *, verify_test: bool) -> str | None:
    """Run the appropriate verify gate for *ws*.

    Respects per-repo ``[verify]`` config keys:
    - ``skip = true`` → returns ``"verify skipped (config)"`` immediately.
    - ``timeout`` → used as the verify timeout (env var still wins).
    - ``script`` → forwarded to the Node path via :func:`verify_node`.
    """
    profile = _load_verify_profile(ws)

    if profile.get("skip"):
        logger.info("verify skipped (config) for %s", ws.path)
        return None

    mgr = ws.manager
    timeout = _resolve_verify_timeout_with_profile(profile)
    if mgr in ("yarn", "npm", "bun", "pnpm"):
        return verify_node(ws)
    if mgr == "go":
        proc = run(["go", "mod", "verify"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "go mod verify failed"
        # Default post-upgrade build gate: catches API-breaking bumps that
        # go mod verify (checksum-only) would pass silently.
        proc_build = run(["go", "build", "./..."], ws.path, timeout=timeout)
        if proc_build.returncode != 0:
            return proc_build.stderr.strip() or "go build ./... failed"
        if verify_test:
            proc2 = run(["go", "test", "./..."], ws.path, timeout=timeout)
            if proc2.returncode != 0:
                return proc2.stderr.strip() or "go test failed"
        return None
    if mgr == "rust":
        proc = run(["cargo", "check"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "cargo check failed"
        return None
    if mgr in ("uv", "hatch"):
        proc = run(["uv", "sync", "--locked"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "uv sync --locked failed"
        if mgr == "hatch":
            return _verify_hatch_fast(ws, timeout=timeout)
        # uv: run ruff check if configured — slot for future per-ecosystem checks.
        d = read_toml_file(ws.path / "pyproject.toml") or {}
        if _has_ruff_config(ws, d):
            proc_ruff = run(
                ["uv", "run", "ruff", "check", "."], ws.path, timeout=timeout
            )
            if proc_ruff.returncode != 0:
                return (
                    proc_ruff.stderr or proc_ruff.stdout
                ).strip() or "ruff check failed"
        return None
    if mgr == "poetry":
        proc = run(["poetry", "check"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "poetry check failed"
        return None
    if mgr == "pip-tools":
        return _verify_pip_tools(ws, timeout=timeout)
    return None


def _parse_req_lines(text: str) -> dict[str, str]:
    """Parse pinned versions from a requirements.txt-format string."""
    versions: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^;\s]+)", line)
        if m:
            versions[m.group(1).lower().replace("_", "-")] = m.group(2)
    return versions


def _verify_pip_tools(ws: Workspace, *, timeout: int | None = None) -> str | None:
    """Verify pip-tools workspace by recompiling requirements.in and checking for drift."""
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    if not req_in.exists():
        return None
    if timeout is None:
        timeout = resolve_timeout_verify()
    if shutil.which("uv"):
        proc = run(
            ["uv", "pip", "compile", str(req_in), "-o", "-", "--quiet"],
            ws.path,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return proc.stderr.strip() or "uv pip compile failed"
        compiled = proc.stdout
    elif shutil.which("pip-compile"):
        proc = run(
            ["pip-compile", "--quiet", "-o", "-", str(req_in)],
            ws.path,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return proc.stderr.strip() or "pip-compile failed"
        compiled = proc.stdout
    else:
        return None
    if not req_txt.exists():
        return "requirements.txt missing after apply"
    current = _parse_req_lines(req_txt.read_text(encoding="utf-8", errors="replace"))
    proposed = _parse_req_lines(compiled)
    if not proposed:
        return None
    drift = {
        pkg
        for pkg in set(current) | set(proposed)
        if current.get(pkg) != proposed.get(pkg)
    }
    if drift:
        return f"requirements.txt is out of sync: {', '.join(sorted(drift))}"
    return None
