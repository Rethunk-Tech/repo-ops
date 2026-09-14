"""Expo SDK alignment from the installed ``expo`` package's ``bundledNativeModules.json``.

``expo install --check``/``--fix`` and expo-doctor compare every dependency that file
lists against its spec (``semver.satisfies``), resolving ``expo/bundledNativeModules.json``
from the app directory and skipping names in its ``expo.install.exclude``. A bump past
the spec builds and tests clean and still breaks the native build, so for a workspace
that depends on ``expo`` each listed package gets a hold at its bundled spec, and
``expo`` itself is held at its installed major (an SDK upgrade is a reviewed change).

Online, the Expo CLI also overlays api.expo.dev's per-SDK versions on this file; the
shipped file is what an offline check uses and needs no network during ``plan``.
"""

from __future__ import annotations

import re

from repo_ops_core.filesystem import read_json_file
from repo_ops_core.holds import HoldRule, workspace_rel
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules
from repo_ops_models import Change, Workspace

_SPEC_RE = re.compile(r"^([~^]?)(\d+)\.(\d+)\.(\d+)$")

# Expo validates dependencies and devDependencies only.
_SECTIONS = ("dependencies", "devDependencies")


def _bounds(spec: str) -> tuple[str, str] | None:
    """``(floor, hold ceiling)`` for a bundled spec; ``None`` for any other range shape."""
    match = _SPEC_RE.match(spec.strip())
    if match is None:
        return None
    op, major, minor, patch = match.groups()
    floor = f"{major}.{minor}.{patch}"
    if op == "~":
        return floor, f"{major}.{minor}"
    if op == "^" and major != "0":
        return floor, major
    if op == "^" and minor != "0":
        return floor, f"0.{minor}"
    return floor, floor


def _excluded(pkg: dict[str, object], name: str, spec: str) -> bool:
    expo = pkg.get("expo")
    install = expo.get("install") if isinstance(expo, dict) else None
    entries = install.get("exclude") if isinstance(install, dict) else None
    if not isinstance(entries, list):
        return False
    for entry in map(str, entries):
        at = entry.rfind("@")
        entry_name, entry_spec = (entry[:at], entry[at + 1 :]) if at > 0 else (entry, "")
        # ponytail: a versioned entry counts only when it names the bundled spec verbatim;
        # Expo also honours a range the bundled spec is a subset of. Add semver subset if one appears.
        if entry_name == name and entry_spec in ("", spec):
            return True
    return False


def _expo_app(
    ws: Workspace,
) -> tuple[dict[str, object], set[str], str, dict[str, str]] | None:
    """``(package.json, declared names, installed expo version, bundled specs)`` for an Expo app."""
    if ws.ecosystem != "node":
        return None
    pkg = read_json_file(ws.path / "package.json") or {}
    declared: set[str] = set()
    for section in _SECTIONS:
        deps = pkg.get(section)
        if isinstance(deps, dict):
            declared.update(map(str, deps))
    if "expo" not in declared:
        return None
    # Hoisted installs keep expo at the repo root; the member directory is checked first.
    for base in dict.fromkeys((ws.path, ws.git_repo)):
        meta = read_json_file(base / "node_modules" / "expo" / "package.json")
        bundled = read_json_file(base / "node_modules" / "expo" / "bundledNativeModules.json")
        if meta and isinstance(meta.get("version"), str) and bundled is not None:
            return pkg, declared, meta["version"], {str(k): str(v) for k, v in bundled.items()}
    return None


def expo_hold(ws: Workspace, package: str) -> HoldRule | None:
    """The Expo SDK ceiling for *package* in *ws*, or ``None`` when Expo does not govern it."""
    app = _expo_app(ws)
    if app is None:
        return None
    pkg, _declared, version, bundled = app
    sdk = version.split(".")[0]
    if package == "expo":
        if _excluded(pkg, package, ""):
            return None
        return HoldRule(
            package=package,
            max_version=sdk,
            workspace=workspace_rel(ws),
            reason=f"expo SDK {sdk}: SDK upgrades are manual",
            source="expo",
            spec=f"~{version}",
        )
    spec = bundled.get(package)
    bounds = _bounds(spec) if spec else None
    if spec is None or bounds is None or _excluded(pkg, package, spec):
        return None
    return HoldRule(
        package=package,
        max_version=bounds[1],
        workspace=workspace_rel(ws),
        reason=f"expo SDK {sdk} bundles {spec}",
        source="expo",
        spec=spec,
    )


def with_expo_alignment(ws: Workspace, changes: list[Change]) -> list[Change]:
    """*changes* plus a move to the bundled spec for each declared package installed outside it.

    Mirrors ``expo install --fix`` in both directions: a package below the spec is
    raised to it and one already past it (a sweep that predates this check) comes back.
    ``to`` is the spec's floor; apply installs the spec itself through the hold.
    """
    app = _expo_app(ws)
    if app is None:
        return changes
    _pkg, declared, _version, bundled = app
    aligned: list[Change] = []
    for name in sorted(declared & bundled.keys()):
        rule = expo_hold(ws, name)
        installed = resolved_version_from_node_modules(
            ws.path, name
        ) or resolved_version_from_node_modules(ws.git_repo, name)
        if rule is None or rule.spec is None or installed is None:
            continue
        floor = rule.spec.lstrip("~^")
        if semver_tuple(installed) < semver_tuple(floor) or not version_satisfies_ceiling(
            installed, rule.max_version
        ):
            aligned.append(Change(name, installed, floor))
    names = {c.name for c in aligned}
    return [c for c in changes if c.name not in names] + aligned
