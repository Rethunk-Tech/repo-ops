"""Node workspace helpers (package manager detection, corepack, yarn workspaces)."""

from __future__ import annotations

from pathlib import Path

from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run


def pm_exec_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "exec"],
        "npm": ["npx", "--yes"],
    }.get(manager, ["npx", "--yes"])


def pm_run_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn", "run"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "run"],
        "npm": ["npm", "run"],
    }.get(manager, ["npm", "run"])


def parse_package_manager(pkg: dict) -> tuple[str, str] | None:
    pm = (pkg.get("packageManager") or "").strip()
    if not pm or "@" not in pm:
        return None
    name, ver = pm.split("@", 1)
    return name.lower(), ver


def lockfile_manager(mdir: Path) -> str | None:
    if (mdir / "package-lock.json").exists():
        return "npm"
    if (mdir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (mdir / "bun.lock").exists() or (mdir / "bun.lockb").exists():
        return "bun"
    if (mdir / "yarn.lock").exists():
        return "yarn"
    return None


def detect_node_manager(mdir: Path) -> tuple[str | None, str | None]:
    pkg_path = mdir / "package.json"
    pkg = read_json_file(pkg_path)
    if not pkg:
        return None, "missing or invalid package.json"
    pm_field = parse_package_manager(pkg)
    lock_mgr = lockfile_manager(mdir)
    if pm_field:
        name, _ver = pm_field
        if lock_mgr and lock_mgr != name:
            return (
                name,
                f"WARN: packageManager={name} but lockfile implies {lock_mgr}; using packageManager",
            )
        return name, None
    if lock_mgr:
        return lock_mgr, None
    return None, "no packageManager field and no lockfile"


def corepack_prepare(manager: str, mdir: Path) -> None:
    run(["corepack", "enable"], mdir, timeout=60)
    pkg = read_json_file(mdir / "package.json") or {}
    pm = (pkg.get("packageManager") or "").strip()
    if pm:
        run(["corepack", "prepare", pm, "--activate"], mdir, timeout=120)


def yarn_workspace_members(mdir: Path) -> set[str]:
    pkg = read_json_file(mdir / "package.json") or {}
    members: set[str] = set()
    ws = pkg.get("workspaces")
    if isinstance(ws, list):
        for entry in ws:
            if isinstance(entry, str):
                members.add(entry.rstrip("/"))
    elif isinstance(ws, dict):
        for entry in ws.get("packages") or []:
            if isinstance(entry, str):
                members.add(entry.rstrip("/"))
    return members


def path_is_yarn_workspace_child(mdir: Path, root: Path, members: set[str]) -> bool:
    try:
        rel = mdir.relative_to(root).as_posix()
    except ValueError:
        return False
    for pattern in members:
        pat = pattern.replace("*", "")
        if rel == pattern.rstrip("/*") or rel.startswith(pat):
            return True
        if pattern.endswith("/*") and rel.startswith(pattern[:-2]):
            return True
    return False
