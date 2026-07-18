"""Node overrides/resolutions audit."""

from __future__ import annotations

import json
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


def package_json_paths(ws_path: Path) -> list[Path]:
    """Return root plus workspace-member ``package.json`` paths.

    Mirrors yarn's workspace glob walk so monorepo pins in ``packages/*/package.json``
    are visible to :func:`overrides_audit`.
    """
    root_pkg = ws_path / "package.json"
    if not root_pkg.is_file():
        return []
    paths = [root_pkg]
    try:
        data = json.loads(root_pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return paths
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        patterns = workspaces.get("packages") or []
    elif isinstance(workspaces, list):
        patterns = workspaces
    else:
        patterns = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        for match in ws_path.glob(pattern):
            pkg = match / "package.json" if match.is_dir() else match
            if pkg.is_file() and pkg not in paths:
                paths.append(pkg)
    return paths


def _pins_from_package_json(pkg: dict[str, object], manager: str) -> dict[str, str]:
    """Collect resolution/override pins from one manifest.

    ``manager`` selects which keys are preferred first; all known pin keys are
    still read so cross-PM monorepo pins are not invisible.
    """
    pins: dict[str, str] = {}
    # Prefer manager-typical keys first so later identical keys from other
    # sections do not clobber the primary pin source for that PM.
    if manager == "yarn":
        key_order = ("resolutions", "overrides")
    elif manager == "pnpm":
        key_order = ("overrides", "resolutions")
    else:
        key_order = ("overrides", "resolutions")
    for key in key_order:
        val = pkg.get(key)
        if isinstance(val, dict):
            pins.update({str(k): str(v) for k, v in val.items()})
    pnpm = pkg.get("pnpm") or {}
    if isinstance(pnpm, dict) and isinstance(pnpm.get("overrides"), dict):
        # Always surface pnpm.overrides (even for yarn/npm workspaces that
        # happen to carry them); for pnpm they are the primary pin source.
        pins.update({str(k): str(v) for k, v in pnpm["overrides"].items()})
    return pins


def overrides_audit(mdir: Path, manager: str) -> list[dict[str, str]]:
    """Warn about stale/blocking overrides across the workspace root and members."""
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

    paths = package_json_paths(mdir)
    if not paths:
        # Non-workspace / missing root: still try the root package.json.
        root = mdir / "package.json"
        paths = [root] if root.is_file() else []

    out: list[dict[str, str]] = []
    seen_pins: set[tuple[str, str]] = set()
    for pkg_path in paths:
        pkg = read_json_file(pkg_path) or {}
        if not isinstance(pkg, dict):
            continue
        pins = _pins_from_package_json(pkg, manager)
        for name, pinned in pins.items():
            dedupe_key = (name, pinned)
            if dedupe_key in seen_pins:
                continue
            seen_pins.add(dedupe_key)
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
                        "detail": (
                            f"OVERRIDE [{manager}] {name} pinned {pinned_ver} — "
                            f"not found in node_modules"
                        ),
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
                            f"OVERRIDE [{manager}] blocks upgrade: {name} pinned "
                            f"{pinned_ver} but tree resolves {resolved}"
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
                            f"OVERRIDE [{manager}] stale: {name} pinned {pinned_ver} "
                            f"but lock/tree has {resolved}"
                        ),
                    }
                )
    return out
