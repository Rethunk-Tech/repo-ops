"""Node.js ecosystem upgrade check/apply and lockfile diff helpers."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from repo_ops.core.filesystem import read_json_file
from repo_ops.core.process import run
from repo_ops.core.version_utils import normalize_version, semver_tuple
from repo_ops.upgrade.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
    TIMEOUT_APPLY,
    YARN_ISOLATED_COPY_SKIP,
    YARN_ISOLATED_YARN_SKIP,
)
from repo_ops.upgrade.models import Change, Workspace
from repo_ops.upgrade.workspaces import corepack_prepare


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
    from repo_ops.upgrade.ecosystems import CHECKERS

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
