"""Post-apply verification gates per ecosystem.

Per-repo verify profiles
------------------------
The ``[verify]`` table in ``.repo-ops.toml`` (or the global config) supports
three knobs:

``verify.script`` (str)
    Overrides gate delegation and the Node.js waterfall (ci → ci:pushgate →
    build → lint) with a specific package.json script name.  Empty string skips
    the script step.  Real-world use: Heroku
    repos that need ``yarn install --immutable && yarn build:production`` as
    their gate can declare ``script = "build:production"`` instead of
    relying on a ``ci`` script.

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
import re
import shutil
from typing import Any

from repo_ops_core.config import (
    load_global_config,
    load_repo_config,
)
from repo_ops_core.constants import GO_MODULE_MODE_ENV, TIMEOUT_VERIFY
from repo_ops_core.filesystem import read_json_file, read_toml_file
from repo_ops_core.process import run
from repo_ops_js.workspaces import pm_run_prefix
from repo_ops_models import Workspace
from repo_ops_py.requirements import parse_requirements_lines
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


def _pyrefly_scope_args(d: dict) -> list[str] | str:
    """Return pyrefly CLI scope args from the target workspace config."""
    pyrefly = (d.get("tool") or {}).get("pyrefly") or {}
    if not isinstance(pyrefly, dict):
        return "invalid [tool.pyrefly] configuration"

    args: list[str] = []
    for key in ("project-includes", "project-excludes"):
        values = pyrefly.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            return f"invalid [tool.pyrefly] {key}: expected a list of strings"
        if values:
            args.extend([f"--{key}", *values])
    return args


def _verify_hatch_fast(
    ws: Workspace,
    *,
    timeout: int | None = None,
    runner: list[str] | None = None,
) -> str | None:
    """Verify a Python workspace, preferring the gates it declares.

    The ladder below is the fallback for a project ``gate`` cannot see: it
    picks one command from what it can infer (a ``test`` script, pytest,
    mypy, pyrefly, ruff) and stops there, so a project declaring more gates
    than that gets the weaker check.
    """
    d = read_toml_file(ws.path / "pyproject.toml")
    if not d:
        return None
    runner = ["uv", "run"] if runner is None else runner
    if runner[0] == "uv" and not shutil.which("uv"):
        return "uv not on PATH"
    scripts = (d.get("project") or {}).get("scripts") or {}
    dev = _pyproject_dev_deps(d)
    if timeout is None:
        timeout = TIMEOUT_VERIFY
    # What the project declares outranks this module's guess at it.
    handled, gate_err = _gate_verify(ws, timeout=timeout)
    if handled:
        return gate_err
    # "test" script wins outright
    if "test" in scripts:
        proc = run([*runner, "test"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or f"{' '.join([*runner, 'test'])} failed"
        return None
    # pytest runner (explicit dep or tests/ dir)
    if "pytest" in dev or (ws.path / "tests").is_dir():
        proc = run([*runner, "pytest", "-q", "--co", "-q"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or f"{' '.join([*runner, 'pytest'])} failed"
        return None
    # "check" or "typecheck" scripts (e.g. mypy / pyright wrappers)
    for script_key in ("check", "typecheck"):
        if script_key in scripts:
            proc = run([*runner, script_key], ws.path, timeout=timeout)
            if proc.returncode != 0:
                return (
                    proc.stderr or proc.stdout
                ).strip() or f"{' '.join([*runner, script_key])} failed"
            return None
    # mypy in dev deps
    if "mypy" in dev:
        proc = run([*runner, "mypy", "."], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or f"{' '.join([*runner, 'mypy'])} failed"
        return None
    # pyrefly in dev deps
    if "pyrefly" in dev:
        scope_args = _pyrefly_scope_args(d)
        if isinstance(scope_args, str):
            return scope_args
        proc = run(
            [*runner, "pyrefly", "check", ".", *scope_args],
            ws.path,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return (
                proc.stderr or proc.stdout
            ).strip() or f"{' '.join([*runner, 'pyrefly', 'check'])} failed"
        return None
    # ruff in dev deps (lint-only fallback)
    if "ruff" in dev:
        proc = run([*runner, "ruff", "check", "."], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (
                proc.stderr or proc.stdout
            ).strip() or f"{' '.join([*runner, 'ruff', 'check'])} failed"
    return None


# Waterfall of default script candidates tried in order. Only reached when the
# gate CLI is unavailable or detects nothing there: gate ranks Makefile targets
# and turbo tasks above package.json scripts, which this waterfall cannot see.
_NODE_VERIFY_CANDIDATES = ("ci", "ci:pushgate", "build", "lint")


def _gate_verify(
    ws: Workspace,
    *,
    timeout: int,
) -> tuple[bool, str | None]:
    """Verify *ws* by running the gates the project itself declares.

    Returns ``(handled, error)``. ``handled`` is False when the ``gate`` CLI is
    absent or detects no gate here, which leaves the caller on its own gates.

    ``gate --json`` executes nothing and exits 0 even when it detects nothing,
    so it is both the availability probe and the gate set: a bare ``gate`` run
    against a project declaring none exits 129, which is not a verify failure.
    Every detected gate runs, tests included: a partial gate reporting green
    is the failure mode verify exists to catch.
    """
    if not shutil.which("gate"):
        # Audible at the default log level: this verify is weaker than what the
        # project declares, and nothing else in the output would say so.
        logger.warning(
            "gate not on PATH; %s falls back to repo-ops' built-in verify gates",
            ws.path,
        )
        return (False, None)
    # JSON, not the --list text: a multi-line inline gate script prints lines
    # indented like gate names, and scraping them ran ``gate run git``.
    listing = run(["gate", "--json"], ws.path, timeout=timeout)
    try:
        # Opt-in e2e gates are listed but skipped by a default gate run; running
        # them by name here is what made verify outlast its timeout.
        names = [
            g["name"] for g in json.loads(listing.stdout).get("gates") or [] if not g.get("skipped")
        ]
    except (ValueError, AttributeError, TypeError, KeyError):
        names = []
    if listing.returncode != 0 or not names:
        logger.info("gate detects no gate for %s; falling back to built-in verify gates", ws.path)
        return (False, None)
    # gate's own per-gate timeout would race this process's; 0 disables it and
    # leaves the caller's verify timeout the single authority.
    proc = run(["gate", "--timeout", "0", "run", *names], ws.path, timeout=timeout)
    if proc.returncode != 0:
        return (True, (proc.stderr or proc.stdout).strip() or f"gate run {' '.join(names)} failed")
    return (True, None)


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


def _resolve_verify_timeout_with_profile(profile: dict[str, Any]) -> int:
    """Resolve the verify timeout from the merged ``[verify]`` profile, else the default."""
    cfg_timeout = profile.get("timeout")
    if isinstance(cfg_timeout, int) and cfg_timeout > 0:
        return cfg_timeout

    return TIMEOUT_VERIFY


def verify_node(ws: Workspace) -> str | None:
    """Run immutable install then the configured verify script for a Node workspace.

    Script resolution order (first match wins):

    1. ``verify.script`` in per-repo / global config (empty → skip script step)
    2. The gates the project declares, run through the ``gate`` CLI
    3. Waterfall: ci → ci:pushgate → build → lint (first found in package.json)
    4. Biome fallback (lint / biome:ci)

    Steps 3 and 4 are reached only when ``gate`` is absent or detects nothing.
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

    cfg_script = profile.get("script")
    if cfg_script is not None:
        # Explicit empty string in config → skip script step.
        if str(cfg_script).strip() == "":
            return None
        script_name = str(cfg_script).strip()
    else:
        # No explicit override: what the project declares outranks this
        # module's guess at it.
        handled, gate_err = _gate_verify(ws, timeout=timeout)
        if handled:
            return gate_err
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


def verify_workspace(ws: Workspace) -> str | None:
    """Run the appropriate verify gate for *ws*.

    Respects per-repo ``[verify]`` config keys:
    - ``skip = true`` → returns ``None`` immediately (gate treated as passed)
      after logging ``verify skipped (config)``.
    - ``timeout`` → used as the verify timeout (env var still wins).
    - ``script`` → forwarded to the Node path via :func:`verify_node`.

    Returns ``None`` on success (or intentional skip); a non-empty string is
    the verify failure detail.
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
        proc = run(["go", "mod", "verify"], ws.path, timeout=timeout, env=GO_MODULE_MODE_ENV)
        if proc.returncode != 0:
            return proc.stderr.strip() or "go mod verify failed"
        handled, gate_err = _gate_verify(ws, timeout=timeout)
        if handled:
            return gate_err
        # Default post-upgrade build gate: catches API-breaking bumps that
        # go mod verify (checksum-only) would pass silently.
        proc_build = run(["go", "build", "./..."], ws.path, timeout=timeout, env=GO_MODULE_MODE_ENV)
        if proc_build.returncode != 0:
            return proc_build.stderr.strip() or "go build ./... failed"
        proc2 = run(["go", "test", "./..."], ws.path, timeout=timeout, env=GO_MODULE_MODE_ENV)
        if proc2.returncode != 0:
            return proc2.stderr.strip() or "go test failed"
        return None
    if mgr == "rust":
        handled, gate_err = _gate_verify(ws, timeout=timeout)
        if handled:
            return gate_err
        proc = run(["cargo", "check"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "cargo check failed"
        proc2 = run(["cargo", "test"], ws.path, timeout=timeout)
        if proc2.returncode != 0:
            return proc2.stderr.strip() or "cargo test failed"
        return None
    if mgr in ("uv", "hatch"):
        # Hatch without uv.lock: apply is a no-op unless migrate-to-uv-lock;
        # cannot run uv sync --locked. Fall through to hatch fast gates only.
        if mgr == "hatch" and not (ws.path / "uv.lock").is_file():
            logger.info(
                "hatch verify: no uv.lock at %s; skipping uv sync --locked",
                ws.path,
            )
            return _verify_hatch_fast(ws, timeout=timeout)
        if not shutil.which("uv"):
            return "uv not on PATH"
        # gate does not check that a lockfile is frozen-installable, so this
        # runs whether or not the declared gates take over below.
        proc = run(["uv", "sync", "--locked"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "uv sync --locked failed"
        return _verify_hatch_fast(ws, timeout=timeout)
    if mgr == "poetry":
        if not shutil.which("poetry"):
            return "poetry not on PATH"
        proc = run(["poetry", "check"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return proc.stderr.strip() or "poetry check failed"
        return _verify_hatch_fast(ws, timeout=timeout, runner=["poetry", "run"])
    if mgr == "pdm":
        if not shutil.which("pdm"):
            return "pdm not on PATH"
        proc = run(["pdm", "sync", "--clean"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "pdm sync --clean failed"
        return _verify_hatch_fast(ws, timeout=timeout, runner=["pdm", "run"])
    if mgr == "pipenv":
        if not shutil.which("pipenv"):
            return "pipenv not on PATH"
        proc = run(["pipenv", "verify"], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "pipenv verify failed"
        return _verify_hatch_fast(ws, timeout=timeout, runner=["pipenv", "run"])
    if mgr == "pip-tools":
        return _verify_pip_tools(ws, timeout=timeout)
    if mgr == "deno":
        if not shutil.which("deno"):
            return "deno not on PATH"
        # Frozen install from deno.lock when present; otherwise type-check.
        if (ws.path / "deno.lock").is_file():
            proc = run(
                ["deno", "install", "--frozen"],
                ws.path,
                timeout=timeout,
            )
            if proc.returncode != 0:
                return (proc.stderr or proc.stdout).strip() or "deno install --frozen failed"
            return None
        proc = run(["deno", "check", "."], ws.path, timeout=timeout)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "deno check failed"
        return None
    if mgr == "precommit":
        if not shutil.which("pre-commit"):
            return "pre-commit not on PATH"
        proc = run(
            ["pre-commit", "validate-config"],
            ws.path,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "pre-commit validate-config failed"
        return None
    # container: image builds are out of scope for v1 (see container.apply docstring).
    return None


def _verify_pip_tools(ws: Workspace, *, timeout: int | None = None) -> str | None:
    """Verify pip-tools workspace by recompiling requirements.in and checking for drift."""
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    if not req_in.exists():
        return None
    if timeout is None:
        timeout = TIMEOUT_VERIFY
    if shutil.which("uv"):
        proc = run(
            ["uv", "pip", "compile", str(req_in), "-o", "-", "--quiet"],
            ws.path,
            timeout=timeout,
        )
        # uv writes `-o -` to stdout and also to a file named `-` in cwd.
        (ws.path / "-").unlink(missing_ok=True)
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
    current = parse_requirements_lines(req_txt.read_text(encoding="utf-8", errors="replace"))
    proposed = parse_requirements_lines(compiled)
    if not proposed:
        return None
    drift = {pkg for pkg in set(current) | set(proposed) if current.get(pkg) != proposed.get(pkg)}
    if drift:
        return f"requirements.txt is out of sync: {', '.join(sorted(drift))}"
    return None
