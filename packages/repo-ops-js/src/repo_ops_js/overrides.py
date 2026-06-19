"""Node overrides/resolutions audit."""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.filesystem import read_json_file
from repo_ops_core.version_utils import normalize_version
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules

from repo_ops_js.guards import _semver_lt_pre


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

    # Matches an exact semver pin: optionally a leading "v", then three
    # dot-separated numeric components, with an optional pre-release / build
    # suffix.  Anything with a leading operator (^, ~, >=, >, <=, <) or
    # wildcard/partial spec (1.x, 1.*, *, 1.2) does NOT match and is treated
    # as a range pin — for which in-range resolution is valid and must not
    # trigger a "stale" warning.
    _EXACT_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+].*)?$")

    out: list[dict[str, str]] = []
    for name, pinned in pins.items():
        # Protocol-style pins are intentional references (monorepo workspace,
        # local path, git URL, etc.) — they are never semver and cannot be
        # stale in the same sense as a version pin.  Skip them entirely so we
        # don't fire spurious "OVERRIDE stale:" warnings.
        if pinned.startswith(_PROTOCOL_PREFIXES):
            continue
        pkg_name = _override_package_name(name)
        # Extract the raw version spec (strip any scoped-package @version suffix).
        raw_spec = pinned.split("@")[-1] if "@" in pinned else pinned
        pinned_ver = normalize_version(raw_spec)
        resolved = resolved_version_from_node_modules(mdir, pkg_name)
        if not resolved:
            out.append(
                {
                    "level": "warn",
                    "detail": f"OVERRIDE {name} pinned {pinned_ver} — not found in node_modules",
                }
            )
            continue
        # Use pre-release-aware comparison so that resolved "4.0.0-rc.1" is
        # correctly identified as less than a stable pin "4.0.0" (semver §11.3).
        if _semver_lt_pre(resolved, raw_spec):
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
        # For range pins (^, ~, >=, x-ranges, partial versions, …) the floor
        # check above already guarantees resolved >= floor.  Any higher
        # in-range resolution is valid — suppress the stale warning.
        # Only fire for exact pins where the resolved version must match exactly.
        # Compare raw values so a pre-release resolved ("4.0.0-rc.1") against an
        # exact stable pin ("4.0.0") correctly triggers the stale warning.
        if _EXACT_RE.match(raw_spec) and resolved != raw_spec:
            out.append(
                {
                    "level": "warn",
                    "detail": (
                        f"OVERRIDE stale: {name} pinned {pinned_ver} but lock/tree has {resolved}"
                    ),
                }
            )
    return out
