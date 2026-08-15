"""Lockfile snapshots and diff helpers shared across node ecosystems."""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_core.filesystem import read_json_file
from repo_ops_core.version_utils import normalize_version
from repo_ops_lockfiles.constants import (
    DENO_LOCKFILES,
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops_models import Change

# Coarse Change entries for lockfiles that lack structured per-package diffs.
# package.json and yarn.lock are handled with dedicated parsers above.
_COARSE_DIFF_LOCKFILES = frozenset(
    {
        *NODE_LOCKFILES,
        *PY_LOCKFILES,
        *GO_LOCKFILES,
        *RUST_LOCKFILES,
        *DENO_LOCKFILES,
    }
    - {"package.json", "yarn.lock"}
)


def resolved_version_from_node_modules(mdir: Path, name: str) -> str | None:
    """Read installed package version from node_modules (flat or scoped layout)."""
    candidates = [
        mdir / "node_modules" / name / "package.json",
    ]
    if name.startswith("@") and "/" in name:
        scope, pkg = name.split("/", 1)
        candidates.append(mdir / "node_modules" / scope / pkg / "package.json")
    for p in candidates:
        d = read_json_file(p)
        if d and d.get("version"):
            return str(d["version"])
    return None


def _parse_yarn_lock_versions(text: str) -> dict[str, str]:
    """Return {range_header: resolved_version} for each block in a yarn.lock text."""
    import re

    out: dict[str, str] = {}
    current_hdr: str | None = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            current_hdr = None
            continue
        if not line[0].isspace():
            # Header line: may be one or more comma-separated range specs followed by ":"
            raw = line.rstrip(":")
            # Use the first descriptor as the canonical key
            hdr = raw.split(",")[0].strip().strip('"')
            current_hdr = hdr if "@" in hdr else None
        elif current_hdr is not None:
            m = re.match(r'^\s+version[:\s]+["\']?([^"\']+)["\']?', line)
            if m:
                out[current_hdr] = m.group(1).strip()
                current_hdr = None  # version captured; stop scanning this block
    return out


def _diff_lock_text(before: str, after: str) -> list[Change]:
    changes: list[Change] = []
    b_map = _parse_yarn_lock_versions(before)
    a_map = _parse_yarn_lock_versions(after)
    for hdr in set(a_map) | set(b_map):
        b_ver = b_map.get(hdr)
        a_ver = a_map.get(hdr)
        if a_ver != b_ver:
            # Derive package name from the range header
            if hdr.startswith("@"):
                at2 = hdr.find("@", 1)
                name = hdr[:at2] if at2 != -1 else hdr
            else:
                name = hdr.split("@")[0]
            changes.append(Change(name, b_ver or "?", a_ver or "removed"))
    return changes[:50]


def snapshot_workspace_files(mdir: Path, manager: str) -> dict[Path, bytes]:
    names: tuple[str, ...]
    if manager in ("yarn", "npm", "pnpm", "bun"):
        names = NODE_LOCKFILES
    elif manager in ("uv", "hatch", "pip-tools", "poetry", "pdm", "pipenv"):
        names = PY_LOCKFILES
    elif manager == "go":
        names = GO_LOCKFILES
    elif manager == "rust":
        names = RUST_LOCKFILES
    elif manager == "deno":
        names = DENO_LOCKFILES
    else:
        names = ()
    if manager == "pnpm":
        names = (*names, "pnpm-workspace.yaml")
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
    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
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


def restore_workspace_snapshot(
    mdir: Path,
    before: dict[Path, bytes],
    manager: str,
) -> None:
    """Restore lockfile/manifest bytes captured before apply."""
    after = snapshot_workspace_files(mdir, manager)
    for path in set(before) | set(after):
        if path in before:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before[path])
        elif path.exists():
            path.unlink()


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
        elif path.name in _COARSE_DIFF_LOCKFILES and path.name not in seen:
            seen.add(path.name)
            changes.append(Change(path.name, "changed", "changed"))
    return changes
