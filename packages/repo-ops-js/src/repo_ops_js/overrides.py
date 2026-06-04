"""Node overrides/resolutions audit."""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.filesystem import read_json_file
from repo_ops_core.version_utils import normalize_version, semver_tuple
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules


def _override_package_name(name: str) -> str:
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

    _PROTOCOL_PREFIXES = (
        "workspace:",
        "file:",
        "link:",
        "portal:",
        "git+",
        "github:",
        "https:",
        "http:",
        "npm:",
        "catalog:",
        "patch:",
    )

    out: list[dict[str, str]] = []
    for name, pinned in pins.items():
        # Protocol-style pins are intentional references (monorepo workspace,
        # local path, git URL, etc.) — they are never semver and cannot be
        # stale in the same sense as a version pin.  Skip them entirely so we
        # don't fire spurious "OVERRIDE stale:" warnings.
        if pinned.startswith(_PROTOCOL_PREFIXES):
            continue
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
