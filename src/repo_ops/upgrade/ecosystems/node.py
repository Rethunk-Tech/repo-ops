"""Node.js ecosystem upgrade check/apply (bun, npm, pnpm) and overrides audit."""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_ops.core.filesystem import read_json_file
from repo_ops.core.process import run
from repo_ops.core.version_utils import normalize_version, semver_tuple
from repo_ops.upgrade.constants import TIMEOUT_APPLY
from repo_ops.upgrade.ecosystems.node_lockfiles import (
    resolved_version_from_node_modules,
)
from repo_ops.upgrade.models import Change, Workspace


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
        resolved = resolved_version_from_node_modules(mdir, pkg_name)
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


def apply_bun(ws: Workspace) -> None:
    proc = run(["bun", "update", "--latest"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")


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
