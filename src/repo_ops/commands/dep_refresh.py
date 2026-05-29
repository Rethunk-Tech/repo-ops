#!/usr/bin/env python3
"""Refresh local dependency trees: dry-run report by default, apply with -y.

Primary day-to-day use:
  dep_refresh.py /usr/local/src/com.github

Default roots (overridable positional args):
  /usr/local/src, ~/Projects, ~/Documents, ~/bastion-ai-helpers, ~/Apps

Usage:
  dep_refresh.py [root_dir ...]              # dry-run report
  dep_refresh.py -y [root_dir ...]           # apply + remediations + verify
  dep_refresh.py -y --no-verify ...          # apply only
  dep_refresh.py --json ...
  dep_refresh.py --filter node|go|rust|py ...
  dep_refresh.py --only-outdated ...
  dep_refresh.py --jobs 4 ...
  dep_refresh.py --fail-fast ...
  dep_refresh.py --skip-remediations biome,playwright ...
  dep_refresh.py --also-actions [root_dir ...]
  dep_refresh.py --only-actions [root_dir ...]
  dep_refresh.py --flat [root_dir]
  dep_refresh.py --allow-dirty ...

Remediation hooks (extensible):
  biome_schema       @biomejs/biome + $schema drift → biome migrate --write
  playwright_browsers  @playwright/test bump → <pm> exec playwright install
  codegen            grpc/openapi deps → generate:proto / generate:openapi
  package_manager_pin  packageManager field stale → rewrite to tool version
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_ops import constants, process
from repo_ops.dep_refresh_models import (
    ActionBump,
    Change,
    RepoBundle,
    Workspace,
    WorkspaceResult,
)
from repo_ops.filesystem import read_json_file, should_exclude
from repo_ops.git import git_repo_for, repo_label
from repo_ops.process import run, set_verbose
from repo_ops.version_utils import normalize_version, semver_tuple

_FAIL_FAST_EVENT = threading.Event()

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

# ---------- Constants (from shared) ----------
SKIP_DIRS = constants.SKIP_DIRS
EXCLUDE_PATH_FRAGMENTS = constants.EXCLUDE_PATH_FRAGMENTS
DEFAULT_ROOTS = constants.DEFAULT_ROOTS

TIMEOUT_CHECK = 600
TIMEOUT_APPLY = 600
TIMEOUT_VERIFY = 900

YARN_ISOLATED_COPY_SKIP = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "out",
    ".turbo",
    ".cache",
    "coverage",
    ".nuxt",
    ".svelte-kit",
    ".dep_refresh_venv",
    "venv",
    ".venv",
}
YARN_ISOLATED_YARN_SKIP = {
    "unplugged",
    "install-state.gz",
    "build-state.yml",
}

GO_UPGRADE_RE = re.compile(r"\[([^\]]+)\]")

USES_RE = re.compile(
    r"^\s*uses:\s+([^\s@]+)@(v[\d.]+|[0-9a-f]{7,40})\s*$",
    re.MULTILINE,
)

ACTION_ALLOWLIST: dict[str, str] = {
    "actions/checkout": "v4",
    "actions/setup-node": "v4",
    "actions/setup-go": "v5",
    "actions/upload-artifact": "v4",
    "actions/download-artifact": "v4",
    "actions/cache": "v4",
    "oven-sh/setup-bun": "v2",
}

CODEGEN_TRIGGERS = (
    "@grpc/",
    "grpc-tools",
    "@accility/protoc",
    "protobufjs",
    "swagger-",
    "openapi-typescript",
    "@openapitools/",
)

BIOME_PKG = "@biomejs/biome"
PLAYWRIGHT_PKG = "@playwright/test"

YARN_PEER_WARN = re.compile(r"YN00(60|02|86)")


# ---------- Helpers ----------


def is_git_dirty(repo: Path) -> bool:
    proc = run(["git", "status", "--porcelain"], repo, timeout=30)
    return proc.returncode == 0 and bool(proc.stdout.strip())


# ---------- Node manager detection ----------


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


# ---------- Workspace discovery ----------


def discover_workspaces_in_root(root: Path, flat: bool) -> list[Workspace]:
    found: list[Workspace] = []
    yarn_roots: dict[Path, set[str]] = {}

    def consider_node(mdir: Path) -> None:
        if not (mdir / "package.json").exists():
            return
        mgr, warn = detect_node_manager(mdir)
        if not mgr:
            print(f"SKIP node {mdir}: {warn}", file=sys.stderr)
            return
        if warn:
            print(f"WARN {mdir}: {warn}", file=sys.stderr)
        for yr, members in yarn_roots.items():
            if mdir != yr and path_is_yarn_workspace_child(mdir, yr, members):
                return
        if mgr == "yarn":
            members = yarn_workspace_members(mdir)
            if members:
                yarn_roots[mdir] = members
        git = git_repo_for(mdir)
        if not git:
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="node",
                manager=mgr,
                git_repo=git.resolve(),
                label="",
            )
        )

    def consider_go(mdir: Path) -> None:
        if (mdir / "go.mod").exists():
            git = git_repo_for(mdir)
            if git:
                found.append(
                    Workspace(
                        path=mdir.resolve(),
                        ecosystem="go",
                        manager="go",
                        git_repo=git.resolve(),
                        label="",
                    )
                )

    def consider_rust(mdir: Path) -> None:
        cargo = mdir / "Cargo.toml"
        if not cargo.exists():
            return
        lock = mdir / "Cargo.lock"
        try:
            d = tomllib.loads(cargo.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            d = {}
        if not lock.exists() and not d.get("workspace") and not d.get("package"):
            return
        git = git_repo_for(mdir)
        if git:
            found.append(
                Workspace(
                    path=mdir.resolve(),
                    ecosystem="rust",
                    manager="rust",
                    git_repo=git.resolve(),
                    label="",
                )
            )

    def consider_py(mdir: Path) -> None:
        pyp = mdir / "pyproject.toml"
        if not (
            pyp.exists()
            or (mdir / "requirements.in").exists()
            or (mdir / "Pipfile").exists()
        ):
            return
        mgr: str | None = None
        if (mdir / "uv.lock").exists():
            mgr = "uv"
        elif (mdir / "poetry.lock").exists():
            mgr = "poetry"
        elif (mdir / "requirements.in").exists() or (
            mdir / "requirements.txt"
        ).exists():
            mgr = "pip-tools"
        elif pyp.exists():
            try:
                d = tomllib.loads(pyp.read_text(encoding="utf-8", errors="replace"))
                build = (d.get("build-system") or {}).get("build-backend", "")
                if "hatch" in build or (d.get("tool") or {}).get("hatch"):
                    mgr = "hatch"
            except Exception:
                pass
        if not mgr:
            return
        git = git_repo_for(mdir)
        if git:
            found.append(
                Workspace(
                    path=mdir.resolve(),
                    ecosystem="py",
                    manager=mgr,
                    git_repo=git.resolve(),
                    label="",
                )
            )

    seen_paths: set[Path] = set()
    if not root.is_dir() or should_exclude(str(root)):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        mdir = Path(dirpath)
        if should_exclude(str(mdir)):
            dirnames.clear()
            continue
        names = set(filenames)
        key = mdir.resolve()
        if key in seen_paths:
            continue
        before = len(found)
        if "package.json" in names:
            consider_node(mdir)
        if "go.mod" in names:
            consider_go(mdir)
        if "Cargo.toml" in names:
            consider_rust(mdir)
        if names & {"pyproject.toml", "requirements.in", "Pipfile"}:
            consider_py(mdir)
        if len(found) > before:
            seen_paths.add(key)

    return found


def discover_all(roots: list[str], flat: bool) -> list[Workspace]:
    all_ws: list[Workspace] = []
    seen: set[tuple[str, str]] = set()
    for r in roots:
        root = Path(r).expanduser().resolve()
        if not root.is_dir():
            print(f"WARN: root not found: {root}", file=sys.stderr)
            continue
        for ws in discover_workspaces_in_root(root, flat=flat and len(roots) == 1):
            key = (str(ws.path), ws.manager)
            if key not in seen:
                seen.add(key)
                all_ws.append(ws)
    return all_ws


def group_by_repo(
    workspaces: list[Workspace], roots: list[str]
) -> dict[Path, RepoBundle]:
    bundles: dict[Path, RepoBundle] = {}
    for ws in workspaces:
        if ws.git_repo not in bundles:
            bundles[ws.git_repo] = RepoBundle(
                git_repo=ws.git_repo,
                label=repo_label(ws.git_repo, roots),
            )
        bundles[ws.git_repo].workspaces.append(WorkspaceResult(workspace=ws))
    return bundles


# ---------- Overrides audit ----------


def _resolved_from_nm(mdir: Path, name: str) -> str | None:
    candidates = [
        mdir / "node_modules" / name / "package.json",
    ]
    if name.startswith("@"):
        scope, pkg = name.split("/", 1)
        candidates.append(mdir / "node_modules" / scope / pkg / "package.json")
    for p in candidates:
        d = read_json_file(p)
        if d and d.get("version"):
            return str(d["version"])
    return None


def _override_package_name(name: str) -> str:
    # npm/pnpm override keys can include version selectors, including scoped names.
    target = name.split(">", 1)[-1].strip()
    if target.startswith("@"):
        m = re.match(r"^(@[^/@]+/[^@/]+)", target)
        return m.group(1) if m else target
    return target.split("@", 1)[0]


def overrides_audit(mdir: Path, manager: str) -> list[dict[str, str]]:
    pkg = read_json_file(mdir / "package.json") or {}
    pins: dict[str, str] = {}
    for key in ("resolutions", "overrides"):
        val = pkg.get(key)
        if isinstance(val, dict):
            pins.update({str(k): str(v) for k, v in val.items()})
    pnpm = pkg.get("pnpm") or {}
    if isinstance(pnpm.get("overrides"), dict):
        pins.update({str(k): str(v) for k, v in pnpm["overrides"].items()})

    out: list[dict[str, str]] = []
    for name, pinned in pins.items():
        pkg_name = _override_package_name(name)
        pinned_ver = normalize_version(
            pinned.split("@")[-1] if "@" in pinned else pinned
        )
        resolved = _resolved_from_nm(mdir, pkg_name)
        if not resolved:
            out.append(
                {
                    "level": "warn",
                    "detail": f"OVERRIDE {name} pinned {pinned_ver} — not found in node_modules",
                }
            )
            continue
        res_norm = normalize_version(resolved)
        if semver_tuple(pinned_ver) > semver_tuple(res_norm):
            out.append(
                {
                    "level": "warn",
                    "detail": (
                        f"OVERRIDE blocks upgrade: {name} pinned {pinned_ver} "
                        f"but tree resolves {resolved}"
                    ),
                }
            )
            continue
        if res_norm != pinned_ver:
            out.append(
                {
                    "level": "warn",
                    "detail": (
                        f"OVERRIDE stale: {name} pinned {pinned_ver} but lock/tree has {resolved}"
                    ),
                }
            )
    return out


# ---------- Check adapters ----------


def check_bun(ws: Workspace) -> list[Change]:
    proc = run(["bun", "outdated"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed"
        )
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] not in ("Package", "bun", "─"):
            changes.append(Change(parts[0], parts[1], parts[-1]))
    if not changes:
        proc2 = run(["bun", "outdated", "--json"], ws.path)
        if proc2.returncode in (0, 1) and proc2.stdout.strip():
            try:
                data = json.loads(proc2.stdout)
                for name, meta in (data if isinstance(data, dict) else {}).items():
                    if isinstance(meta, dict):
                        cur = meta.get("current") or meta.get("installed", "?")
                        lat = meta.get("latest", "?")
                        if cur != lat:
                            changes.append(Change(name, str(cur), str(lat)))
            except json.JSONDecodeError:
                pass
    return changes


def check_npm(ws: Workspace) -> list[Change]:
    proc = run(["npm", "outdated", "--json"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "npm outdated failed")
    if not proc.stdout.strip():
        return []
    data = json.loads(proc.stdout)
    changes: list[Change] = []
    for name, meta in data.items():
        if isinstance(meta, dict):
            changes.append(
                Change(
                    name,
                    str(meta.get("current", "?")),
                    str(meta.get("latest", "?")),
                )
            )
    return changes


def check_pnpm(ws: Workspace) -> list[Change]:
    proc = run(["pnpm", "outdated", "--format", "json"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "pnpm outdated failed")
    changes: list[Change] = []
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
            items = data if isinstance(data, list) else data.get("packages", data)
            if isinstance(items, dict):
                for name, meta in items.items():
                    if isinstance(meta, dict):
                        changes.append(
                            Change(
                                name,
                                str(meta.get("current", "?")),
                                str(meta.get("latest", "?")),
                            )
                        )
            elif isinstance(items, list):
                for meta in items:
                    if isinstance(meta, dict) and meta.get("name"):
                        changes.append(
                            Change(
                                str(meta["name"]),
                                str(meta.get("current", "?")),
                                str(meta.get("latest", "?")),
                            )
                        )
        except json.JSONDecodeError:
            pass
    return changes


def check_go(ws: Workspace) -> list[Change]:
    proc = run(["go", "list", "-u", "-m", "all"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go list failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = GO_UPGRADE_RE.search(line)
        if m:
            parts = line.split()
            name = parts[0]
            cur = parts[1] if len(parts) > 1 and not parts[1].startswith("[") else "?"
            changes.append(Change(name, cur, m.group(1)))
    return changes


def check_rust(ws: Workspace) -> list[Change]:
    proc = run(["cargo", "update", "--dry-run"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update --dry-run failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = re.search(r"Updating\s+(\S+)\s+v([\d.]+)\s+->\s+v([\d.]+)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes


def check_uv(ws: Workspace) -> list[Change]:
    proc = run(["uv", "lock", "--upgrade", "--dry-run"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        if "would upgrade" in line.lower() or "->" in line:
            m = re.search(r"(\S+)\s+([\d.]+)\s*->\s*([\d.]+)", line)
            if m:
                changes.append(Change(m.group(1), m.group(2), m.group(3)))
    if not changes and "would change" in (proc.stdout + proc.stderr).lower():
        changes.append(Change("(lockfile)", "current", "upgraded"))
    return changes


def check_poetry(ws: Workspace) -> list[Change]:
    if not shutil.which("poetry"):
        print(f"WARN {ws.path}: poetry not on PATH", file=sys.stderr)
        return []
    proc = run(["poetry", "update", "--dry-run"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update --dry-run failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = re.search(r"Updating\s+(\S+)\s+\(([^)]+)\s+->\s+([^)]+)\)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes


def _parse_requirements_lines(text: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*==\s*(\S+)", line)
        if m:
            versions[m.group(1).lower().replace("_", "-")] = m.group(2)
    return versions


def check_pip_tools(ws: Workspace) -> list[Change]:
    req_in = ws.path / "requirements.in"
    if not req_in.exists():
        return []
    req_txt = ws.path / "requirements.txt"
    current: dict[str, str] = {}
    if req_txt.exists():
        current = _parse_requirements_lines(
            req_txt.read_text(encoding="utf-8", errors="replace")
        )
    proposed: dict[str, str] = {}
    if shutil.which("uv"):
        proc = run(
            ["uv", "pip", "compile", str(req_in), "-o", "-", "--quiet"],
            ws.path,
            timeout=TIMEOUT_CHECK,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            proposed = _parse_requirements_lines(proc.stdout)
    if not proposed and shutil.which("pip-compile"):
        proc = run(
            ["pip-compile", "--dry-run", "--quiet", "-o", "-", str(req_in)],
            ws.path,
            timeout=TIMEOUT_CHECK,
        )
        if proc.returncode != 0:
            proc = run(
                ["pip-compile", "--upgrade", "--quiet", "-o", "-", str(req_in)],
                ws.path,
                timeout=TIMEOUT_CHECK,
            )
        if proc.returncode == 0 and proc.stdout.strip():
            proposed = _parse_requirements_lines(proc.stdout)
        elif proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "pip-compile failed")
    if not proposed:
        return []
    changes: list[Change] = []
    for pkg in sorted(set(current) | set(proposed)):
        cur = current.get(pkg)
        prop = proposed.get(pkg)
        if cur != prop and prop:
            changes.append(Change(pkg, cur or "?", prop))
    return changes


def check_hatch(ws: Workspace) -> list[Change]:
    if (ws.path / "uv.lock").exists():
        return check_uv(ws)
    if shutil.which("uv"):
        venv_dir = ws.path / ".dep_refresh_venv"
        changes: list[Change] = []
        try:
            run(["uv", "venv", str(venv_dir)], ws.path, timeout=120)
            run(
                ["uv", "pip", "install", "-e", ".[dev]"],
                ws.path,
                timeout=300,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            proc = run(
                ["uv", "pip", "list", "--outdated"],
                ws.path,
                timeout=120,
                env={"VIRTUAL_ENV": str(venv_dir)},
            )
            for line in proc.stdout.splitlines()[2:]:
                parts = line.split()
                if len(parts) >= 3:
                    changes.append(Change(parts[0], parts[1], parts[2]))
        finally:
            shutil.rmtree(venv_dir, ignore_errors=True)
        return changes
    print(f"WARN {ws.path}: hatch project without uv on PATH", file=sys.stderr)
    return []


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


NODE_LOCKFILES = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "package.json",
)
PY_LOCKFILES = ("uv.lock", "requirements.txt", "poetry.lock")
GO_LOCKFILES = ("go.sum", "go.mod")
RUST_LOCKFILES = ("Cargo.lock",)


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


def _isolated_yarn_ignore(src: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in YARN_ISOLATED_COPY_SKIP}
    if Path(src).name == ".yarn":
        ignored.update(name for name in names if name in YARN_ISOLATED_YARN_SKIP)
    return ignored


def _copy_yarn_workspace_to_temp(ws: Workspace, temp_root: Path) -> Path:
    try:
        rel_ws = ws.path.relative_to(ws.git_repo)
        source_root = ws.git_repo
    except ValueError:
        rel_ws = Path(".")
        source_root = ws.path
    dest_root = temp_root / source_root.name
    shutil.copytree(
        source_root,
        dest_root,
        symlinks=True,
        ignore=_isolated_yarn_ignore,
        ignore_dangling_symlinks=True,
    )
    return (dest_root / rel_ws).resolve()


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
    checker = CHECKERS.get(ws.manager)
    if checker:
        try:
            return checker(ws)
        except Exception:
            pass
    return []


def check_yarn_snapshot(ws: Workspace) -> tuple[list[Change], list[dict[str, str]]]:
    changes: list[Change] = []
    snapshot_overrides: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="dep-refresh-yarn-") as td:
        mdir = _copy_yarn_workspace_to_temp(ws, Path(td))
        before_snap = snapshot_workspace_files(mdir, "yarn")
        corepack_prepare("yarn", mdir)
        proc = run(["yarn", "up", "-R", "*"], mdir, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "yarn up failed")
        proc2 = run(["yarn", "install"], mdir, timeout=TIMEOUT_APPLY)
        if proc2.returncode != 0:
            raise RuntimeError(proc2.stderr.strip() or "yarn install failed")
        after_snap = snapshot_workspace_files(mdir, "yarn")
        changes = diff_workspace_snapshots(before_snap, after_snap, "yarn")
        pkg_path = mdir / "package.json"
        pkg_before = before_snap.get(pkg_path, b"")
        pkg_after = after_snap.get(pkg_path, b"")
        if pkg_before != pkg_after and not any(
            c.name == "package.json" for c in changes
        ):
            changes.append(Change("package.json", "changed", "changed"))
        snapshot_overrides = overrides_audit(mdir, "yarn")
    return changes, snapshot_overrides


def _check_yarn(ws: Workspace) -> list[Change]:
    changes, _overrides = check_yarn_snapshot(ws)
    return changes


CHECKERS: dict[str, Callable[[Workspace], list[Change]]] = {
    "bun": check_bun,
    "npm": check_npm,
    "pnpm": check_pnpm,
    "yarn": _check_yarn,
    "go": check_go,
    "rust": check_rust,
    "uv": check_uv,
    "poetry": check_poetry,
    "pip-tools": check_pip_tools,
    "hatch": check_hatch,
}


def check_workspace(ws: Workspace) -> WorkspaceResult:
    result = WorkspaceResult(workspace=ws)
    try:
        if ws.ecosystem == "node" and ws.manager != "yarn":
            corepack_prepare(ws.manager, ws.path)
        checker = CHECKERS.get(ws.manager)
        if not checker:
            result.status = "skip"
            result.error = f"unsupported manager {ws.manager}"
            return result
        if ws.manager == "yarn":
            changes, snap_overrides = check_yarn_snapshot(ws)
            result.changes = changes
            result.overrides_audit = snap_overrides
        else:
            result.changes = checker(ws)
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
    except Exception as exc:
        result.status = "fail"
        result.error = str(exc)
    return result


# ---------- Apply adapters ----------


def apply_bun(ws: Workspace) -> None:
    proc = run(["bun", "update", "--latest"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")


def apply_yarn(ws: Workspace) -> None:
    corepack_prepare("yarn", ws.path)
    proc = run(["yarn", "up", "-R", "*"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yarn up failed")
    proc2 = run(["yarn", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "yarn install failed")


def apply_npm(ws: Workspace) -> None:
    proc = run(["npm", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "npm update failed")
    proc2 = run(["npm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "npm install failed")


def apply_pnpm(ws: Workspace) -> None:
    proc = run(["pnpm", "update", "--latest", "-r"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pnpm update failed")
    proc2 = run(["pnpm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "pnpm install failed")


def apply_go(ws: Workspace) -> None:
    for args in (["go", "get", "-u", "./..."], ["go", "get", "-t", "-u", "./..."]):
        proc = run(args, ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"{' '.join(args)} failed")
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go mod tidy failed")


def apply_rust(ws: Workspace) -> None:
    proc = run(["cargo", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update failed")


def apply_uv(ws: Workspace) -> None:
    proc = run(["uv", "lock", "--upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "uv lock --upgrade failed")
    proc2 = run(["uv", "sync"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "uv sync failed")


def apply_poetry(ws: Workspace) -> None:
    proc = run(["poetry", "update"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "poetry update failed")


def apply_pip_tools(ws: Workspace) -> None:
    req_in = ws.path / "requirements.in"
    req_txt = ws.path / "requirements.txt"
    out = req_txt if req_txt.exists() else req_in.with_suffix(".txt")
    args = ["pip-compile", "--upgrade"]
    if req_in.exists():
        args.extend(["-o", str(out), str(req_in)])
    proc = run(args, ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pip-compile failed")


def apply_hatch(ws: Workspace) -> None:
    if not (ws.path / "uv.lock").exists():
        proc = run(["uv", "lock"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "uv lock failed")
    apply_uv(ws)


APPLIERS: dict[str, Callable[[Workspace], None]] = {
    "bun": apply_bun,
    "yarn": apply_yarn,
    "npm": apply_npm,
    "pnpm": apply_pnpm,
    "go": apply_go,
    "rust": apply_rust,
    "uv": apply_uv,
    "poetry": apply_poetry,
    "pip-tools": apply_pip_tools,
    "hatch": apply_hatch,
}


def apply_workspace(ws: Workspace) -> None:
    if ws.ecosystem == "node":
        corepack_prepare(ws.manager, ws.path)
    applier = APPLIERS.get(ws.manager)
    if not applier:
        raise RuntimeError(f"no applier for {ws.manager}")
    applier(ws)


# ---------- Remediations ----------


@dataclass
class Remediation:
    id: str
    detect: Callable[[Workspace, dict[str, Any]], bool]
    report: Callable[[Workspace, dict[str, Any]], list[str]]
    apply: Callable[[Workspace, dict[str, Any]], bool]


def _find_biome_configs(mdir: Path) -> list[Path]:
    configs: list[Path] = []
    for name in ("biome.json", "biome.jsonc"):
        p = mdir / name
        if p.exists():
            configs.append(p)
    for pattern in yarn_workspace_members(mdir) or []:
        if "*" not in pattern:
            sub = mdir / pattern
            for name in ("biome.json", "biome.jsonc"):
                if (sub / name).exists():
                    configs.append(sub / name)
    return configs


def _biome_installed_version(mdir: Path) -> str | None:
    nm = mdir / "node_modules" / "@biomejs" / "biome" / "package.json"
    d = read_json_file(nm)
    if d:
        return str(d.get("version", ""))
    pkg = read_json_file(mdir / "package.json") or {}
    for section in ("dependencies", "devDependencies"):
        ver = (pkg.get(section) or {}).get(BIOME_PKG)
        if ver:
            return normalize_version(str(ver))
    return None


def _schema_version_from_config(p: Path) -> str | None:
    d = read_json_file(p)
    if not d:
        return None
    schema = d.get("$schema", "")
    m = re.search(r"/schemas/([\d.]+)/", str(schema))
    return m.group(1) if m else None


def _pm_exec_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "exec"],
        "npm": ["npx", "--yes"],
    }.get(manager, ["npx", "--yes"])


def _pm_run_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn", "run"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "run"],
        "npm": ["npm", "run"],
    }.get(manager, ["npm", "run"])


def detect_biome(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if not _find_biome_configs(ws.path):
        return False
    outdated = {c.name for c in ctx.get("changes", [])}
    if BIOME_PKG in outdated or "biome" in outdated:
        return True
    for cfg in _find_biome_configs(ws.path):
        inst = _biome_installed_version(ws.path)
        sch = _schema_version_from_config(cfg)
        if inst and sch and semver_tuple(inst) != semver_tuple(sch):
            return True
    return False


def report_biome(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    inst = _biome_installed_version(ws.path) or "?"
    for cfg in _find_biome_configs(ws.path):
        sch = _schema_version_from_config(cfg) or "?"
        if semver_tuple(inst) != semver_tuple(sch):
            lines.append(
                f"REMEDIATE biome: {cfg.name} $schema {sch} → {inst} "
                f"(would run biome migrate --write)"
            )
    return lines


def apply_biome(ws: Workspace, ctx: dict[str, Any]) -> bool:
    prefix = _pm_exec_prefix(ws.manager)
    ok = True
    for cfg in _find_biome_configs(ws.path):
        cfg_dir = cfg.parent
        cmd = [*prefix, "biome", "migrate", "--write"]
        if cfg_dir != ws.path:
            cmd.append(f"--config-path={cfg_dir}")
        proc = run(cmd, ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            ok = False
    return ok


def _playwright_version_changed(ws: Workspace, ctx: dict[str, Any]) -> bool:
    for c in ctx.get("changes", []):
        if c.name == PLAYWRIGHT_PKG or PLAYWRIGHT_PKG in c.name:
            if c.from_ver not in ("?", "changed", "prior") and c.to not in (
                "?",
                "changed",
                "updated",
            ):
                return normalize_version(c.from_ver) != normalize_version(c.to)
            return True
    return False


def _playwright_missing_browsers(ws: Workspace) -> bool:
    prefix = _pm_exec_prefix(ws.manager)
    proc = run(
        [*prefix, "playwright", "install", "--dry-run"],
        ws.path,
        timeout=120,
    )
    if proc.returncode != 0:
        return False
    combined = (proc.stdout + proc.stderr).lower()
    return any(
        tok in combined
        for tok in (
            "download",
            "installing",
            "missing",
            "will download",
            "needs install",
        )
    )


def detect_playwright(ws: Workspace, ctx: dict[str, Any]) -> bool:
    pkg = read_json_file(ws.path / "package.json") or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if PLAYWRIGHT_PKG not in deps:
        return False
    outdated = {c.name for c in ctx.get("changes", [])}
    if PLAYWRIGHT_PKG in outdated or any(PLAYWRIGHT_PKG in n for n in outdated):
        return True
    if _playwright_version_changed(ws, ctx):
        return True
    return _playwright_missing_browsers(ws)


def report_playwright(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [f"REMEDIATE playwright: would run {ws.manager} exec playwright install"]


def apply_playwright(ws: Workspace, ctx: dict[str, Any]) -> bool:
    prefix = _pm_exec_prefix(ws.manager)
    proc = run([*prefix, "playwright", "install"], ws.path, timeout=TIMEOUT_APPLY)
    return proc.returncode == 0


def detect_codegen(ws: Workspace, ctx: dict[str, Any]) -> bool:
    pkg = read_json_file(ws.path / "package.json") or {}
    scripts = pkg.get("scripts") or {}
    if not any(k in scripts for k in ("generate:proto", "generate:openapi")):
        return False
    outdated = {c.name for c in ctx.get("changes", [])}
    return any(any(t in n for t in CODEGEN_TRIGGERS) for n in outdated)


def report_codegen(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    pkg = read_json_file(ws.path / "package.json") or {}
    scripts = pkg.get("scripts") or {}
    lines: list[str] = []
    for script in ("generate:proto", "generate:openapi"):
        if script in scripts:
            lines.append(f"REMEDIATE codegen: would run {ws.manager} run {script}")
    return lines


def apply_codegen(ws: Workspace, ctx: dict[str, Any]) -> bool:
    prefix = _pm_run_prefix(ws.manager)
    ok = True
    pkg = read_json_file(ws.path / "package.json") or {}
    for script in ("generate:proto", "generate:openapi"):
        if script in (pkg.get("scripts") or {}):
            proc = run([*prefix, script], ws.path, timeout=TIMEOUT_APPLY)
            if proc.returncode != 0:
                ok = False
    return ok


def detect_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> bool:
    pkg = read_json_file(ws.path / "package.json") or {}
    pm = (pkg.get("packageManager") or "").strip()
    if not pm or "@" not in pm:
        return False
    name, pinned = pm.split("@", 1)
    proc = run([name, "--version"], ws.path, timeout=30)
    if proc.returncode != 0:
        return False
    actual = proc.stdout.strip().lstrip("v")
    return normalize_version(pinned) != normalize_version(actual)


def report_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    pkg = read_json_file(ws.path / "package.json") or {}
    pm = (pkg.get("packageManager") or "").strip()
    name, pinned = pm.split("@", 1)
    proc = run([name, "--version"], ws.path, timeout=30)
    actual = proc.stdout.strip().lstrip("v") if proc.returncode == 0 else "?"
    return [f"REMEDIATE package_manager_pin: {pm} → {name}@{actual}"]


def apply_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> bool:
    pkg_path = ws.path / "package.json"
    pkg = read_json_file(pkg_path)
    if not pkg:
        return False
    pm = (pkg.get("packageManager") or "").strip()
    if not pm or "@" not in pm:
        return False
    name, _ = pm.split("@", 1)
    proc = run([name, "--version"], ws.path, timeout=30)
    if proc.returncode != 0:
        return False
    actual = proc.stdout.strip().lstrip("v")
    pkg["packageManager"] = f"{name}@{actual}"
    pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    return True


REMEDIATIONS: list[Remediation] = [
    Remediation("biome_schema", detect_biome, report_biome, apply_biome),
    Remediation(
        "playwright_browsers", detect_playwright, report_playwright, apply_playwright
    ),
    Remediation("codegen", detect_codegen, report_codegen, apply_codegen),
    Remediation("package_manager_pin", detect_pm_pin, report_pm_pin, apply_pm_pin),
]


def _remediation_files(rem_id: str, ws: Workspace) -> list[str]:
    if rem_id == "biome_schema":
        return [str(p.relative_to(ws.path)) for p in _find_biome_configs(ws.path)]
    if rem_id == "package_manager_pin":
        return ["package.json"]
    return []


def run_remediations(
    result: WorkspaceResult,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    ctx: dict[str, Any] = {"changes": result.changes, "apply": apply}
    for rem in REMEDIATIONS:
        if rem.id in skip:
            continue
        entry: dict[str, Any] = {"id": rem.id, "status": "ok", "detail": ""}
        try:
            detected = rem.detect(result.workspace, ctx)
            if process.VERBOSE:
                print(
                    f"  remediation {rem.id}: detect={detected}",
                    file=sys.stderr,
                    flush=True,
                )
            if detected:
                files = _remediation_files(rem.id, result.workspace)
                if files:
                    entry["files"] = files
                lines = rem.report(result.workspace, ctx)
                entry["detail"] = "; ".join(lines)
                if apply:
                    entry["status"] = (
                        "applied" if rem.apply(result.workspace, ctx) else "fail"
                    )
                    if entry["status"] == "fail":
                        result.status = "fail"
                else:
                    entry["status"] = "pending"
            else:
                entry["status"] = "ok"
        except Exception as exc:
            entry["status"] = "fail"
            entry["detail"] = str(exc)
            if apply:
                result.status = "fail"
        if entry.get("status") != "ok" or entry.get("detail"):
            result.remediations.append(entry)


# ---------- Verify gates ----------


def _pyproject_dev_deps(ws: Workspace) -> set[str]:
    pyp = ws.path / "pyproject.toml"
    if not pyp.exists():
        return set()
    try:
        d = tomllib.loads(pyp.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return set()
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
    pyp = ws.path / "pyproject.toml"
    if not pyp.exists():
        return None
    try:
        d = tomllib.loads(pyp.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    scripts = (d.get("project") or {}).get("scripts") or {}
    dev = _pyproject_dev_deps(ws)
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

    run_prefix = {
        "yarn": ["yarn", "run"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "run"],
        "npm": ["npm", "run"],
    }.get(mgr, ["npm", "run"])
    proc = run([*run_prefix, script_name], ws.path, timeout=TIMEOUT_VERIFY)
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


# ---------- GitHub Actions ----------


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def action_latest_tag(action: str) -> str | None:
    if action in ACTION_ALLOWLIST:
        return ACTION_ALLOWLIST[action]
    if not shutil.which("gh"):
        return None
    proc = run(
        ["gh", "api", f"repos/{action}/releases/latest", "--jq", ".tag_name"],
        Path.cwd(),
        timeout=30,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    proc2 = run(
        ["gh", "api", f"repos/{action}/tags", "--jq", ".[0].name"],
        Path.cwd(),
        timeout=30,
    )
    if proc2.returncode == 0 and proc2.stdout.strip():
        return proc2.stdout.strip()
    return None


def scan_actions(repo: Path, apply: bool) -> list[ActionBump]:
    if not shutil.which("actionlint"):
        print(
            "WARN: actionlint not on PATH; skipping action validation", file=sys.stderr
        )
    bumps: list[ActionBump] = []
    for wf in discover_workflow_files(repo):
        text = wf.read_text(encoding="utf-8", errors="replace")
        if shutil.which("actionlint"):
            proc = run(["actionlint", str(wf)], repo, timeout=60)
            if proc.returncode != 0:
                bumps.append(
                    ActionBump(
                        workflow=str(wf.relative_to(repo)),
                        action="(actionlint)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=proc.stderr.strip() or proc.stdout.strip(),
                    )
                )
                continue
        for m in USES_RE.finditer(text):
            action, pin = m.group(1), m.group(2)
            if pin.startswith("sha") or re.fullmatch(r"[0-9a-f]{7,40}", pin):
                bumps.append(
                    ActionBump(
                        workflow=str(wf.relative_to(repo)),
                        action=action,
                        pin=pin,
                        latest=pin,
                        status="ok",
                        detail="SHA-pinned; report only",
                    )
                )
                continue
            latest = action_latest_tag(action)
            if not latest:
                continue
            if pin != latest:
                bump = ActionBump(
                    workflow=str(wf.relative_to(repo)),
                    action=action,
                    pin=pin,
                    latest=latest,
                    status="ok",
                )
                bumps.append(bump)
                if apply:
                    new_text = text.replace(f"{action}@{pin}", f"{action}@{latest}", 1)
                    wf.write_text(new_text, encoding="utf-8")
                    text = new_text
    return bumps


# ---------- Orchestration ----------


def process_workspace(
    result: WorkspaceResult,
    *,
    do_apply: bool,
    do_verify: bool,
    verify_test: bool,
    skip_remediations: set[str],
    allow_dirty: bool,
) -> WorkspaceResult:
    ws = result.workspace
    if _FAIL_FAST_EVENT.is_set():
        result.status = "skip"
        result.error = "fail-fast"
        return result

    if should_exclude(str(ws.path)):
        result.status = "skip"
        result.error = "excluded path"
        return result

    if do_apply:
        if is_git_dirty(ws.git_repo) and not allow_dirty:
            result.status = "skip"
            result.error = "dirty git worktree (use --allow-dirty)"
            return result
        before_snap = snapshot_workspace_files(ws.path, ws.manager)
        try:
            apply_workspace(ws)
            after_snap = snapshot_workspace_files(ws.path, ws.manager)
            result.changes = diff_workspace_snapshots(
                before_snap, after_snap, ws.manager
            )
            if not result.changes:
                result.changes = collect_changes_after_apply(ws)
            if ws.ecosystem == "node":
                result.overrides_audit = overrides_audit(ws.path, ws.manager)
        except Exception as exc:
            result.status = "fail"
            result.error = str(exc)
            return result
    else:
        result = check_workspace(ws)

    run_remediations(result, apply=do_apply, skip=skip_remediations)

    if do_apply and do_verify and result.status != "fail":
        verr = verify_workspace(ws, verify_test=verify_test)
        if verr:
            result.verify_error = verr
            result.status = "fail"

    return result


def filter_workspaces(
    workspaces: list[Workspace],
    eco_filter: str | None,
) -> list[Workspace]:
    if not eco_filter:
        return workspaces
    return [w for w in workspaces if w.ecosystem == eco_filter]


def print_human_report(
    bundles: dict[Path, RepoBundle],
    *,
    dry_run: bool,
    only_outdated: bool,
    top_n: int,
    also_actions: bool,
) -> None:
    mode = "dry-run" if dry_run else "apply"
    print(f"dep_refresh ({mode})")
    print()
    for repo in sorted(bundles.values(), key=lambda b: b.label):
        print(f"## {repo.label}")
        for wr in repo.workspaces:
            ws = wr.workspace
            n = len(wr.changes)
            if only_outdated and n == 0 and wr.status == "ok":
                continue
            status = wr.status.upper()
            rel = ws.path
            try:
                rel = ws.path.relative_to(ws.git_repo)
            except ValueError:
                pass
            print(f"  [{status}] {rel} ({ws.ecosystem}/{ws.manager}) — {n} upgrade(s)")
            if wr.error:
                print(f"    error: {wr.error}")
            if wr.verify_error:
                print(f"    verify: {wr.verify_error}")
            for c in wr.changes[:top_n]:
                print(f"    {c.name} {c.from_ver} → {c.to}")
            if len(wr.changes) > top_n:
                print(f"    ... and {len(wr.changes) - top_n} more")
            for oa in wr.overrides_audit:
                print(f"    {oa.get('detail', oa)}")
            for rem in wr.remediations:
                if rem.get("status") != "ok" or rem.get("detail"):
                    print(
                        f"    [{rem.get('id')}] {rem.get('detail') or rem.get('status')}"
                    )
        if also_actions and repo.actions:
            print("  Actions:")
            for ab in repo.actions:
                print(
                    f"    {ab.workflow}: {ab.action}@{ab.pin} → {ab.latest} [{ab.status}]"
                )
        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh local dependencies (dry-run by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("roots", nargs="*", help="Root directories to scan")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Apply updates and remediations"
    )
    parser.add_argument(
        "--no-verify", action="store_true", help="Skip verify gates after apply"
    )
    parser.add_argument("--json", action="store_true", help="JSON report")
    parser.add_argument(
        "--filter", choices=["node", "go", "rust", "py"], dest="eco_filter"
    )
    parser.add_argument(
        "--only-outdated", action="store_true", help="Omit clean workspaces"
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--skip-remediations", default="", metavar="IDS")
    parser.add_argument("--also-actions", action="store_true")
    parser.add_argument("--only-actions", action="store_true")
    parser.add_argument("--flat", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--verify-test", action="store_true", help="Run go test ./... in verify"
    )
    parser.add_argument(
        "--top", type=int, default=10, help="Max changes shown per workspace"
    )
    args = parser.parse_args()

    roots = [str(Path(r).expanduser()) for r in (args.roots or DEFAULT_ROOTS)]
    skip_remediations = {
        s.strip() for s in args.skip_remediations.split(",") if s.strip()
    }

    workspaces: list[Workspace] = []
    if not args.only_actions:
        workspaces = discover_all(roots, flat=args.flat)
        workspaces = filter_workspaces(workspaces, args.eco_filter)

    bundles = group_by_repo(workspaces, roots)

    if args.only_actions:
        common: dict[Path, RepoBundle] = {}
        for r in roots:
            root = Path(r).expanduser().resolve()
            if not root.is_dir():
                continue
            candidates = [root]
            for dirpath, dirnames, _filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                if should_exclude(dirpath):
                    dirnames.clear()
                    continue
                candidates.append(Path(dirpath))
            for cand in candidates:
                if not (cand / ".git").exists():
                    continue
                git = git_repo_for(cand)
                if not git or should_exclude(str(git)):
                    continue
                if git not in common:
                    common[git] = RepoBundle(
                        git_repo=git,
                        label=repo_label(git, roots),
                    )
        bundles = common

    set_verbose(args.verbose)
    _FAIL_FAST_EVENT.clear()

    def _process_one(wr: WorkspaceResult) -> WorkspaceResult:
        return process_workspace(
            wr,
            do_apply=args.yes,
            do_verify=args.yes and not args.no_verify,
            verify_test=args.verify_test,
            skip_remediations=skip_remediations,
            allow_dirty=args.allow_dirty,
        )

    def _process_repo(bundle: RepoBundle) -> list[WorkspaceResult]:
        results: list[WorkspaceResult] = []
        for wr in bundle.workspaces:
            updated = _process_one(wr)
            results.append(updated)
            if args.fail_fast and updated.status == "fail":
                _FAIL_FAST_EVENT.set()
                break
        return results

    all_results: list[WorkspaceResult] = []
    if args.yes:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            pending_bundles: list[RepoBundle] = [
                bundle for bundle in bundles.values() if bundle.workspaces
            ]
            futures: dict[
                concurrent.futures.Future[list[WorkspaceResult]], RepoBundle
            ] = {}
            while pending_bundles or futures:
                while (
                    pending_bundles
                    and len(futures) < args.jobs
                    and not _FAIL_FAST_EVENT.is_set()
                ):
                    bundle = pending_bundles.pop(0)
                    fut = pool.submit(_process_repo, bundle)
                    futures[fut] = bundle
                if not futures:
                    break
                done, _ = concurrent.futures.wait(
                    futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for fut in done:
                    futures.pop(fut)
                    updated = fut.result()
                    all_results.extend(updated)
                    if args.fail_fast and any(r.status == "fail" for r in updated):
                        _FAIL_FAST_EVENT.set()
                        for pending_fut in list(futures):
                            pending_fut.cancel()
                        futures.clear()
                        pending_bundles.clear()
    else:
        all_wrs = [wr for bundle in bundles.values() for wr in bundle.workspaces]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            pending: list[WorkspaceResult] = list(all_wrs)
            futures: dict[  # type: ignore[no-redef]
                concurrent.futures.Future[WorkspaceResult], WorkspaceResult
            ] = {}
            while pending or futures:
                while (
                    pending
                    and len(futures) < args.jobs
                    and not _FAIL_FAST_EVENT.is_set()
                ):
                    wr = pending.pop(0)
                    fut = pool.submit(_process_one, wr)  # type: ignore[arg-type]
                    futures[fut] = wr  # type: ignore[assignment]
                if not futures:
                    break
                done, _ = concurrent.futures.wait(
                    futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for fut in done:
                    futures.pop(fut)
                    updated = fut.result()
                    all_results.append(updated)  # type: ignore[arg-type]
                    if args.fail_fast and updated.status == "fail":  # type: ignore[attr-defined]
                        _FAIL_FAST_EVENT.set()
                        for pending_fut in list(futures):
                            pending_fut.cancel()
                        futures.clear()
                        pending.clear()

    for bundle in bundles.values():
        path_to_result = {r.workspace.path: r for r in all_results}
        bundle.workspaces = [
            path_to_result.get(wr.workspace.path, wr) for wr in bundle.workspaces
        ]

    if args.also_actions or args.only_actions:
        for bundle in bundles.values():
            if bundle.any_fail() and not args.only_actions:
                continue
            bundle.actions = scan_actions(bundle.git_repo, apply=args.yes)

    if args.only_outdated:
        for bundle in bundles.values():
            bundle.workspaces = [
                wr
                for wr in bundle.workspaces
                if wr.changes or wr.status != "ok" or wr.remediations
            ]

    if args.json:
        payload = {
            "roots": roots,
            "dry_run": not args.yes,
            "repos": [
                b.to_json() for b in sorted(bundles.values(), key=lambda x: x.label)
            ],
        }
        print(json.dumps(payload, indent=2))
    elif not args.quiet:
        print_human_report(
            bundles,
            dry_run=not args.yes,
            only_outdated=args.only_outdated,
            top_n=args.top,
            also_actions=args.also_actions or args.only_actions,
        )

    any_fail = any(
        wr.status == "fail" for b in bundles.values() for wr in b.workspaces
    ) or any(a.status == "fail" for b in bundles.values() for a in b.actions)
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
