"""Post-apply verification gates per ecosystem."""

from __future__ import annotations

from repo_ops.core.filesystem import read_json_file, read_toml_file
from repo_ops.core.process import run
from repo_ops.upgrade.constants import TIMEOUT_VERIFY, YARN_PEER_WARN
from repo_ops.upgrade.models import Workspace
from repo_ops.upgrade.workspaces import pm_run_prefix


def _pyproject_dev_deps(d: dict) -> set[str]:
    dev: set[str] = set()
    opt = (d.get("project") or {}).get("optional-dependencies") or {}
    for vals in opt.values():
        if isinstance(vals, list):
            for spec in vals:
                if isinstance(spec, str):
                    dev.add(spec.split("[")[0].strip().lower())
    tool = d.get("tool") or {}
    hatch_envs = (tool.get("hatch") or {}).get("envs") or {}
    for env_cfg in hatch_envs.values():
        if isinstance(env_cfg, dict):
            for dep in env_cfg.get("dependencies") or []:
                if isinstance(dep, str):
                    dev.add(dep.split("[")[0].strip().lower())
    return dev


def _verify_hatch_fast(ws: Workspace) -> str | None:
    d = read_toml_file(ws.path / "pyproject.toml")
    if not d:
        return None
    scripts = (d.get("project") or {}).get("scripts") or {}
    dev = _pyproject_dev_deps(d)
    if "test" in scripts:
        proc = run(["uv", "run", "test"], ws.path, timeout=TIMEOUT_VERIFY)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run test failed"
        return None
    if "pytest" in dev or (ws.path / "tests").is_dir():
        proc = run(
            ["uv", "run", "pytest", "-q", "--co", "-q"], ws.path, timeout=TIMEOUT_VERIFY
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run pytest failed"
        return None
    if "ruff" in dev:
        proc = run(["uv", "run", "ruff", "check", "."], ws.path, timeout=TIMEOUT_VERIFY)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "uv run ruff check failed"
    return None


def verify_node(ws: Workspace) -> str | None:
    mgr = ws.manager
    pkg = read_json_file(ws.path / "package.json") or {}
    scripts = pkg.get("scripts") or {}

    lock_cmds = {
        "yarn": ["yarn", "install", "--immutable"],
        "npm": ["npm", "ci"],
        "bun": ["bun", "install", "--frozen-lockfile"],
        "pnpm": ["pnpm", "install", "--frozen-lockfile"],
    }
    lock = lock_cmds.get(mgr)
    if lock:
        proc = run(lock, ws.path, timeout=TIMEOUT_VERIFY)
        combined = proc.stdout + proc.stderr
        if proc.returncode != 0 or (mgr == "yarn" and YARN_PEER_WARN.search(combined)):
            return combined.strip() or f"{mgr} immutable install failed"

    script_name: str | None = None
    for candidate in ("ci", "ci:pushgate", "build", "lint"):
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

    proc = run([*pm_run_prefix(mgr), script_name], ws.path, timeout=TIMEOUT_VERIFY)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or f"{script_name} failed"
    return None


def verify_workspace(ws: Workspace, *, verify_test: bool) -> str | None:
    mgr = ws.manager
    if mgr in ("yarn", "npm", "bun", "pnpm"):
        return verify_node(ws)
    if mgr == "go":
        proc = run(["go", "mod", "verify"], ws.path, timeout=120)
        if proc.returncode != 0:
            return proc.stderr.strip() or "go mod verify failed"
        if verify_test:
            proc2 = run(["go", "test", "./..."], ws.path, timeout=TIMEOUT_VERIFY)
            if proc2.returncode != 0:
                return proc2.stderr.strip() or "go test failed"
        return None
    if mgr == "rust":
        proc = run(["cargo", "check"], ws.path, timeout=TIMEOUT_VERIFY)
        if proc.returncode != 0:
            return proc.stderr.strip() or "cargo check failed"
        return None
    if mgr in ("uv", "hatch"):
        proc = run(["uv", "sync", "--locked"], ws.path, timeout=TIMEOUT_VERIFY)
        if proc.returncode != 0:
            return proc.stderr.strip() or "uv sync --locked failed"
        if mgr == "hatch":
            return _verify_hatch_fast(ws)
        return None
    if mgr == "poetry":
        proc = run(["poetry", "check"], ws.path, timeout=120)
        if proc.returncode != 0:
            return proc.stderr.strip() or "poetry check failed"
        return None
    return None
