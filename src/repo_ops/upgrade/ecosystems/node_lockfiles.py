"""Lockfile snapshots and diff helpers shared across node ecosystems."""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_ops.core.process import run
from repo_ops.core.version_utils import normalize_version
from repo_ops.upgrade.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops.upgrade.models import Change, Workspace


def _diff_lock_text(before: str, after: str) -> list[Change]:
    changes: list[Change] = []
    b_map: dict[str, str] = {}
    a_map: dict[str, str] = {}
    for line in before.splitlines():
        if not line or line.startswith("#"):
            continue
        hdr = line.split()[0]
        if "@" in hdr:
            b_map[hdr] = hdr
    for line in after.splitlines():
        if not line or line.startswith("#"):
            continue
        hdr = line.split()[0]
        if "@" in hdr:
            a_map[hdr] = hdr
    for hdr in set(a_map) | set(b_map):
        if a_map.get(hdr) != b_map.get(hdr):
            if hdr.startswith("@"):
                parts = hdr.split("@")
                name = f"@{parts[1]}" if len(parts) > 2 else hdr
            else:
                name = hdr.split("@")[0]
            changes.append(
                Change(name, "?" if hdr not in b_map else "prior", "updated")
            )
    return changes[:50]


def snapshot_workspace_files(mdir: Path, manager: str) -> dict[Path, bytes]:
    names: tuple[str, ...]
    if manager in ("yarn", "npm", "pnpm", "bun"):
        names = NODE_LOCKFILES
    elif manager in ("uv", "hatch", "pip-tools", "poetry"):
        names = PY_LOCKFILES
    elif manager == "go":
        names = GO_LOCKFILES
    elif manager == "rust":
        names = RUST_LOCKFILES
    else:
        names = ()
    out: dict[Path, bytes] = {}
    for name in names:
        p = mdir / name
        if p.exists():
            out[p] = p.read_bytes()
    return out


def _pkg_json_dep_changes(before: bytes, after: bytes) -> list[Change]:
    try:
        b_pkg = json.loads(before.decode("utf-8", errors="replace"))
        a_pkg = json.loads(after.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    changes: list[Change] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        b_sec = (b_pkg.get(section) or {}) if isinstance(b_pkg, dict) else {}
        a_sec = (a_pkg.get(section) or {}) if isinstance(a_pkg, dict) else {}
        for name in set(b_sec) | set(a_sec):
            bv = (
                normalize_version(str(b_sec.get(name, ""))) if b_sec.get(name) else None
            )
            av = (
                normalize_version(str(a_sec.get(name, ""))) if a_sec.get(name) else None
            )
            if bv != av and av:
                changes.append(Change(name, bv or "?", av))
    return changes


def diff_workspace_snapshots(
    before: dict[Path, bytes],
    after: dict[Path, bytes],
    manager: str,
) -> list[Change]:
    changes: list[Change] = []
    seen: set[str] = set()
    for path in set(before) | set(after):
        b = before.get(path, b"")
        a = after.get(path, b"")
        if b == a:
            continue
        if path.name == "package.json":
            for c in _pkg_json_dep_changes(b, a):
                if c.name not in seen:
                    seen.add(c.name)
                    changes.append(c)
        elif path.name == "yarn.lock":
            for c in _diff_lock_text(
                b.decode("utf-8", errors="replace"),
                a.decode("utf-8", errors="replace"),
            ):
                if c.name not in seen:
                    seen.add(c.name)
                    changes.append(c)
        elif path.name in (
            "package-lock.json",
            "pnpm-lock.yaml",
            "uv.lock",
            "requirements.txt",
        ):
            if path.name not in seen:
                seen.add(path.name)
                changes.append(Change(path.name, "changed", "changed"))
    return changes


def collect_changes_after_apply(ws: Workspace) -> list[Change]:
    """Lightweight post-apply change detection for remediation context."""
    repo = ws.git_repo
    rel = ws.path
    try:
        rel = ws.path.relative_to(repo)
    except ValueError:
        rel = ws.path
    rel_s = str(rel)
    lock_names = (
        list(NODE_LOCKFILES)
        + list(PY_LOCKFILES)
        + list(GO_LOCKFILES)
        + list(RUST_LOCKFILES)
    )
    proc = run(
        ["git", "diff", "--name-only", "--", *lock_names],
        repo,
        timeout=60,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        changes: list[Change] = []
        for line in proc.stdout.splitlines():
            p = line.strip()
            if not p.startswith(rel_s):
                continue
            fname = Path(p).name
            if fname == "package.json":
                proc2 = run(
                    ["git", "diff", "--", p],
                    repo,
                    timeout=60,
                )
                if proc2.returncode == 0:
                    m = re.findall(
                        r'^[+-]\s*"([^"]+)":\s*"([^"]+)"',
                        proc2.stdout,
                        re.MULTILINE,
                    )
                    for name, ver in m:
                        if name in ("dependencies", "devDependencies"):
                            continue
                        changes.append(Change(name, "?", normalize_version(ver)))
            elif fname.endswith(".lock") or fname in (
                "go.sum",
                "go.mod",
                "requirements.txt",
            ):
                changes.append(Change(fname, "changed", "updated"))
        if changes:
            return changes
    from repo_ops.upgrade.ecosystems import CHECKERS

    checker = CHECKERS.get(ws.manager)
    if checker:
        try:
            return checker(ws)
        except Exception:
            pass
    return []
