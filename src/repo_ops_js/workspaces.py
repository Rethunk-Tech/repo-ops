"""Node workspace helpers (package manager detection, corepack, yarn workspaces)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.holds import target_within_hold
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_js.holds import resolve_package_hold
from repo_ops_js.overrides import package_json_paths
from repo_ops_models import Change, Workspace

KNOWN_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})

# Managers Corepack ships shims for. Anything else (notably bun) is a hard
# "Unsupported package manager specification" Usage Error, so Corepack is
# skipped entirely and the real binary on PATH is used instead.
COREPACK_MANAGERS = frozenset({"npm", "pnpm", "yarn"})


def pm_argv(manager: str, *args: str) -> list[str]:
    """Invoke a Node package manager without relying on a process-global shim.

    ``corepack <pm> …`` reads ``packageManager`` for that cwd, so parallel
    applies cannot steal an ``--activate``d default from each other.
    """
    if manager in COREPACK_MANAGERS:
        return ["corepack", manager, *args]
    return [manager, *args]


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
    if (mdir / "bun.lock").exists():
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
    proc_prepare = run(["corepack", "prepare", pm], mdir, timeout=120)
    if proc_prepare.returncode != 0:
        print(
            f"WARN corepack prepare {pm} failed: {_proc_error(proc_prepare)}",
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


def _in_member(changes: list[Change], root: Workspace, member: Workspace) -> list[Change]:
    """Tag *changes* with their member so reporting resolves member-scoped holds there."""
    rel = member.path.relative_to(root.path).as_posix()
    return [replace(c, member=rel) for c in changes]


_SIMPLE_SPEC_RE = re.compile(r"^([\^~]?)(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")


def peer_lockstep(ws: Workspace, planned: list[Change], *, write: bool) -> list[Change]:
    """Peer specs in private members that trail the version the same manifest depends on.

    A private member is consumed only inside its monorepo, so its peer range states
    what the repo builds against: an exact pin moves to the member's own dependency
    version and a ``^``/``~`` floor is raised to it. A published package's peer range
    is a compatibility contract for outside consumers and is left alone, as is a peer
    the member does not also depend on. Following the member's own spec rather than
    the repo's newest copy keeps one app's member-scoped hold from raising a shared
    package's peer floor past it. *planned* supplies this run's pending member bumps.
    """
    targets = {(c.member, c.name): c.to for c in planned}
    moved: list[Change] = []
    for member in member_workspaces(ws):
        pkg_path = member.path / "package.json"
        pkg = read_json_file(pkg_path) or {}
        peers = pkg.get("peerDependencies")
        if pkg.get("private") is not True or not isinstance(peers, dict):
            continue
        rel = member.path.relative_to(ws.path).as_posix()
        deps: dict[str, object] = {}
        for section in ("optionalDependencies", "devDependencies", "dependencies"):
            if isinstance(pkg.get(section), dict):
                deps.update(pkg[section])
        member_moved: list[Change] = []
        for name, spec in peers.items():
            peer = _SIMPLE_SPEC_RE.match(str(spec))
            dep = _SIMPLE_SPEC_RE.match(str(targets.get((rel, name)) or deps.get(name) or ""))
            if peer is None or dep is None:
                continue
            version = dep.group(2)
            if version == peer.group(2) or semver_tuple(version) < semver_tuple(peer.group(2)):
                continue
            rule = resolve_package_hold(member, name)
            if not target_within_hold(version, rule):
                continue
            member_moved.append(Change(name, str(spec), peer.group(1) + version, rel))
        if write and member_moved:
            for change in member_moved:
                peers[change.name] = change.to
            pkg_path.write_text(
                json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        moved.extend(member_moved)
    return moved


def check_with_members(
    ws: Workspace, check_dir: Callable[[Workspace], tuple[list[Change], list[Change]]]
) -> tuple[list[Change], list[Change]]:
    """Run a one-directory check at *ws* and at each member, merging the results."""
    changes, behind = check_dir(ws)
    for member in member_workspaces(ws):
        member_changes, member_behind = check_dir(member)
        merge_changes(changes, _in_member(member_changes, ws, member))
        merge_changes(behind, _in_member(member_behind, ws, member))
    merge_changes(changes, peer_lockstep(ws, changes, write=False))
    return changes, behind


def apply_with_members(
    ws: Workspace, apply_dir: Callable[[Workspace, Path], list[Change]]
) -> list[Change]:
    """Run a one-directory apply at *ws* and at each member; *apply_dir* gets the lockfile root."""
    behind = apply_dir(ws, ws.path)
    for member in member_workspaces(ws):
        merge_changes(behind, _in_member(apply_dir(member, ws.path), ws, member))
    if peer_lockstep(ws, [], write=True):
        # bun.lock mirrors member peer ranges and --frozen-lockfile passes a stale copy.
        proc = run(pm_argv(ws.manager, "install"), ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(f"{ws.manager} install after peer lockstep: {_proc_error(proc)}")
    return behind
