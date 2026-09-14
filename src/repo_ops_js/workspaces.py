"""Node workspace helpers (package manager detection, corepack, yarn workspaces)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run
from repo_ops_js.holds import direct_dependency_names
from repo_ops_js.overrides import package_json_paths
from repo_ops_models import Change, Workspace

KNOWN_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})

# Managers Corepack ships shims for. Anything else (notably bun) is a hard
# "Unsupported package manager specification" Usage Error, so Corepack is
# skipped entirely and the real binary on PATH is used instead.
COREPACK_MANAGERS = frozenset({"npm", "pnpm", "yarn"})


def pm_exec_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn"],
        "bun": ["bunx"],
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
    name = name.strip().lower()
    ver = ver.strip()
    if not name or not ver:
        return None
    if name not in KNOWN_PACKAGE_MANAGERS:
        return None
    return name, ver


def pm_version_argv(manager: str) -> list[str] | None:
    if manager not in KNOWN_PACKAGE_MANAGERS:
        return None
    return [manager, "--version"]


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


def _proc_error(proc: subprocess.CompletedProcess[str]) -> str:
    """Failure text for a warn line; Corepack prints Usage Errors on stdout."""
    return proc.stderr.strip() or proc.stdout.strip()


def corepack_prepare(manager: str, mdir: Path) -> None:
    """Activate the workspace's pinned package manager via Corepack.

    A no-op for managers Corepack does not support (see COREPACK_MANAGERS).
    """
    if manager not in COREPACK_MANAGERS:
        return
    proc_enable = run(["corepack", "enable"], mdir, timeout=60)
    if proc_enable.returncode != 0:
        print(
            f"WARN corepack enable failed: {_proc_error(proc_enable)}",
            file=sys.stderr,
        )
    pkg = read_json_file(mdir / "package.json") or {}
    pm = (pkg.get("packageManager") or "").strip()
    if not pm:
        return
    if pm.split("@", 1)[0].strip().lower() not in COREPACK_MANAGERS:
        return
    proc_prepare = run(["corepack", "prepare", pm, "--activate"], mdir, timeout=120)
    if proc_prepare.returncode != 0:
        print(
            f"WARN corepack prepare {pm} --activate failed: {_proc_error(proc_prepare)}",
            file=sys.stderr,
        )


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
        if rel == pattern.rstrip("/*") or _under(rel, pat):
            return True
    return False


def _under(rel: str, prefix: str) -> bool:
    """True when *rel* equals *prefix* or sits under it on a path boundary.

    Separator-aware so ``packages/foo`` does not match the sibling
    ``packages/foobar``.
    """
    prefix = prefix.rstrip("/")
    return rel == prefix or rel.startswith(prefix + "/")


def member_workspaces(ws: Workspace) -> list[Workspace]:
    """A monorepo root's members, each as a ``Workspace`` at its own directory.

    Discovery folds members into the root (one shared lockfile), but bun and pnpm
    scope ``outdated``/``add``/``update`` to the cwd's project and npm's targeted
    install saves into the cwd's manifest, so member manifests are only reached
    by running those from each member directory. ``path`` being the member also
    scopes hold rules to it.
    """
    return [replace(ws, path=p.parent) for p in package_json_paths(ws.path)[1:]]


def merge_changes(into: list[Change], more: list[Change]) -> None:
    """Append *more* to *into*, dropping exact duplicates (one dep shared by members)."""
    for change in more:
        if change not in into:
            into.append(change)


def member_audit_overrides(ws: Workspace, root: Path, overrides: dict[str, str]) -> dict[str, str]:
    """The advisory overrides a directory may install: all at the root, own deps in a member.

    Installing from a member directory saves into that member's manifest, so an
    undeclared name would become a new direct dependency of the member.
    """
    if ws.path == root:
        return overrides
    declared = direct_dependency_names(read_json_file(ws.path / "package.json") or {})
    return {name: ver for name, ver in overrides.items() if name in declared}


def _in_member(changes: list[Change], root: Workspace, member: Workspace) -> list[Change]:
    """Tag *changes* with their member so reporting resolves member-scoped holds there."""
    rel = member.path.relative_to(root.path).as_posix()
    return [replace(c, member=rel) for c in changes]


def check_with_members(
    ws: Workspace, check_dir: Callable[[Workspace], tuple[list[Change], list[Change]]]
) -> tuple[list[Change], list[Change]]:
    """Run a one-directory check at *ws* and at each member, merging the results."""
    changes, behind = check_dir(ws)
    for member in member_workspaces(ws):
        member_changes, member_behind = check_dir(member)
        merge_changes(changes, _in_member(member_changes, ws, member))
        merge_changes(behind, _in_member(member_behind, ws, member))
    return changes, behind


def apply_with_members(
    ws: Workspace, apply_dir: Callable[[Workspace, Path], list[Change]]
) -> list[Change]:
    """Run a one-directory apply at *ws* and at each member; *apply_dir* gets the lockfile root."""
    behind = apply_dir(ws, ws.path)
    for member in member_workspaces(ws):
        merge_changes(behind, _in_member(apply_dir(member, ws.path), ws, member))
    return behind
