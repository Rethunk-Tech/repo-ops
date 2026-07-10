"""License-policy gate: scan dependency licenses and report/flag violations.

Scope (best-effort)
-------------------
- npm: reads package.json under node_modules/** (requires prior npm/yarn/pnpm
  install; no network calls).
- Python: reads the ``license`` field of each ``pyproject.toml`` found in the
  scanned tree (the repo's own package manifests), plus the locked third-party
  dependency set of each ``uv.lock``, resolving license ids from an adjacent
  virtualenv's ``*.dist-info/METADATA`` when one exists (#144).  Offline-first:
  locked packages without installed metadata are reported as UNKNOWN rather
  than resolved over the network.  ``setup.cfg`` is not read.
- Go: go.sum module@version pairs resolved via LICENSE* files in the local
  GOMODCACHE (offline; classified from license text heuristics).
- Rust: Cargo.lock crates resolved via ``cargo metadata``'s ``license`` field
  (offline/``--offline``; skipped gracefully when ``cargo`` is unavailable).
- Deno: deno.lock ``npm:`` specifiers resolved via the ``package.json``
  ``license`` field cached under DENO_DIR's npm registry mirror (offline);
  ``jsr:``/``http(s):`` specifiers have no local license source.
- container images: no local license source; reported UNKNOWN and excluded
  from the ``--fail-unknown`` exit-2 bucket (see ``NO_EXTRACTOR_ECOSYSTEMS``).

Limitations are documented in the output when unknown licenses are found.
"""

from __future__ import annotations

import argparse
import contextlib
import email
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_core.cli import (
    add_roots_argument,
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import read_toml_file

from repo_ops_lockfiles.parsers import versions_deno_lock

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

UNKNOWN_LICENSE = "UNKNOWN"


@dataclass
class PackageLicense:
    name: str
    version: str
    license_id: str
    ecosystem: str
    source: str  # e.g. "node_modules", "pyproject.toml"


@dataclass
class PolicyResult:
    violations: list[PackageLicense] = field(default_factory=list)
    unknown: list[PackageLicense] = field(default_factory=list)
    allowed: list[PackageLicense] = field(default_factory=list)
    policy_mode: str = "allow"  # "allow" or "deny"


# ---------------------------------------------------------------------------
# npm: scan node_modules for package.json license fields
# ---------------------------------------------------------------------------

# node_modules subdirs we never descend into
_NM_SKIP = SKIP_DIRS - {"node_modules"}


def scan_npm_licenses(root: Path) -> list[PackageLicense]:
    """Return PackageLicense entries for every package.json under node_modules."""
    results: list[PackageLicense] = []
    nm = root / "node_modules"
    if not nm.exists():
        return results

    for dirpath_str, dirnames, filenames in os.walk(nm):
        dp = Path(dirpath_str)
        # Prune deeply nested node_modules to avoid infinite descent
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _NM_SKIP and not (dp != nm and d == "node_modules")
        ]
        if "package.json" not in filenames or dp == nm:
            continue
        pkg_json = dp / "package.json"
        with contextlib.suppress(Exception):
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            pkg_name = str(data.get("name") or dp.name)
            pkg_ver = str(data.get("version") or "?")
            raw_lic = data.get("license") or UNKNOWN_LICENSE
            # SPDX license object: {"type": "MIT"}
            if isinstance(raw_lic, dict):
                raw_lic = raw_lic.get("type") or UNKNOWN_LICENSE
            # DEPRECATED licenses array (rare, old npm packages)
            if isinstance(raw_lic, list):
                raw_lic = " OR ".join(
                    (
                        str(entry.get("type") or UNKNOWN_LICENSE)
                        if isinstance(entry, dict)
                        else str(entry)
                    )
                    for entry in raw_lic
                )
            lic_id = str(raw_lic).strip() or UNKNOWN_LICENSE
            results.append(
                PackageLicense(
                    name=pkg_name,
                    version=pkg_ver,
                    license_id=lic_id,
                    ecosystem="npm",
                    source="node_modules",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Python: scan pyproject.toml for declared license (manifest-only; does not
# resolve installed/locked dependency licenses)
# ---------------------------------------------------------------------------


def _extract_pyproject_license(data: dict[str, object]) -> str | None:
    """Best-effort license extraction from a parsed pyproject.toml dict."""
    proj = data.get("project") or {}
    if not isinstance(proj, dict):
        return None

    # PEP 517: license = "MIT" (string) or license = {text="..."} / {file="..."}
    lic = proj.get("license")
    if isinstance(lic, str) and lic:
        return lic
    if isinstance(lic, dict):
        text = lic.get("text")
        if isinstance(text, str) and text:
            return text

    # Classifiers: "License :: OSI Approved :: MIT License"
    for clf in proj.get("classifiers") or []:
        if isinstance(clf, str) and clf.startswith("License ::"):
            parts = clf.split("::")
            if len(parts) >= 3:
                return parts[-1].strip()

    # Poetry: [tool.poetry] license = "MIT"
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            plic = poetry.get("license")
            if isinstance(plic, str) and plic:
                return plic

    return None


def scan_python_licenses(root: Path) -> list[PackageLicense]:
    """Return PackageLicense for pyproject.toml entries found under *root*."""
    results: list[PackageLicense] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "pyproject.toml" not in filenames:
            continue
        dp = Path(dirpath_str)
        pyp = dp / "pyproject.toml"
        with contextlib.suppress(Exception):
            data = read_toml_file(pyp) or {}
            proj = data.get("project") or {}
            if not isinstance(proj, dict):
                continue
            pkg_name = proj.get("name")
            if not isinstance(pkg_name, str) or not pkg_name:
                continue
            pkg_ver = str(proj.get("version") or "?")
            lic_id = _extract_pyproject_license(data) or UNKNOWN_LICENSE
            results.append(
                PackageLicense(
                    name=pkg_name,
                    version=pkg_ver,
                    license_id=lic_id,
                    ecosystem="python",
                    source="pyproject.toml",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Python: resolve locked third-party dependency licenses from uv.lock plus an
# adjacent virtualenv's *.dist-info/METADATA (#144).  Offline-first: no
# network; locked packages without installed metadata stay UNKNOWN.
# ---------------------------------------------------------------------------

_PY_NAME_NORM_RE = re.compile(r"[-_.]+")


def _norm_py_name(name: str) -> str:
    """PEP 503 package-name normalisation (lowercase; runs of ``-_.`` → ``-``)."""
    return _PY_NAME_NORM_RE.sub("-", name).lower()


def _license_from_metadata(text: str) -> str | None:
    """Extract a license id from a dist-info METADATA document.

    Preference order: PEP 639 ``License-Expression`` (a proper SPDX
    expression), then a ``License ::`` trove classifier, then a short classic
    ``License`` header — multi-line or very long values are license body text
    pasted into the field, not an identifier, and are ignored.
    """
    msg = email.message_from_string(text)
    expr = msg.get("License-Expression")
    if expr and expr.strip():
        return expr.strip()
    for clf in msg.get_all("Classifier") or []:
        if clf.startswith("License ::"):
            parts = clf.split("::")
            if len(parts) >= 3:
                label = parts[-1].strip()
                if label:
                    return label
    lic = (msg.get("License") or "").strip()
    if lic and lic.upper() != UNKNOWN_LICENSE and "\n" not in lic and len(lic) <= 64:
        return lic
    return None


def _dist_info_license_index(lock_dir: Path) -> dict[str, str]:
    """Map normalised package name → license id from venv dist-info METADATA.

    Looks for ``.venv`` / ``venv`` next to the lockfile, in both the POSIX
    (``lib/python*/site-packages``) and Windows (``Lib/site-packages``)
    layouts.  Versions are intentionally not matched against the lock: license
    metadata comes from whatever is installed (best-effort).
    """
    index: dict[str, str] = {}
    for venv_name in (".venv", "venv"):
        venv = lock_dir / venv_name
        if not venv.is_dir():
            continue
        site_packages = list(venv.glob("lib/python*/site-packages"))
        site_packages.append(venv / "Lib" / "site-packages")
        for sp in site_packages:
            if not sp.is_dir():
                continue
            for dist_info in sp.glob("*.dist-info"):
                # "{name}-{version}.dist-info"; the name part never contains
                # "-" (wheel escaping replaces runs of -_. with _).
                dist_name = dist_info.name[: -len(".dist-info")].rsplit("-", 1)[0]
                meta = dist_info / "METADATA"
                with contextlib.suppress(OSError):
                    lic = _license_from_metadata(
                        meta.read_text(encoding="utf-8", errors="replace")
                    )
                    if lic:
                        index[_norm_py_name(dist_name)] = lic
    return index


def scan_python_dep_licenses(root: Path) -> list[PackageLicense]:
    """Return PackageLicense for third-party packages locked in uv.lock files.

    For each ``uv.lock`` under *root*, every resolved ``[[package]]`` entry is
    reported except workspace members (``editable``/``virtual`` sources — those
    are the repo's own manifests, covered by :func:`scan_python_licenses`).
    License ids come from an adjacent virtualenv's dist-info metadata when
    present; packages without installed metadata are UNKNOWN (offline-first,
    no network resolution).
    """
    results: list[PackageLicense] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "uv.lock" not in filenames:
            continue
        lock_dir = Path(dirpath_str)
        data = read_toml_file(lock_dir / "uv.lock") or {}
        packages = data.get("package")
        if not isinstance(packages, list):
            continue
        licenses = _dist_info_license_index(lock_dir)
        for entry in packages:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            source = entry.get("source")
            if isinstance(source, dict) and (
                "editable" in source or "virtual" in source
            ):
                continue
            lic = licenses.get(_norm_py_name(name))
            results.append(
                PackageLicense(
                    name=name,
                    version=str(entry.get("version") or "?"),
                    license_id=lic or UNKNOWN_LICENSE,
                    ecosystem="python",
                    source="uv.lock+dist-info" if lic else "uv.lock",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Go: classify LICENSE* files in the local GOMODCACHE (offline; no network)
# ---------------------------------------------------------------------------

_GO_LICENSE_FILENAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING")

# Coarse, offline license-text classifier: (SPDX id, required substrings) in
# priority order.  Best-effort only — a handful of distinguishing phrases per
# license family, checked most-specific-first (e.g. BSD-3-Clause's "endorse
# or promote" clause before the more general BSD-2-Clause text it contains).
_LICENSE_TEXT_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Apache-2.0", ("Apache License", "Version 2.0")),
    ("MPL-2.0", ("Mozilla Public License", "2.0")),
    ("GPL-3.0", ("GNU GENERAL PUBLIC LICENSE", "Version 3")),
    ("GPL-2.0", ("GNU GENERAL PUBLIC LICENSE", "Version 2")),
    ("LGPL-3.0", ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 3")),
    ("LGPL-2.1", ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 2.1")),
    (
        "BSD-3-Clause",
        (
            "Redistribution and use in source and binary forms",
            "endorse or promote products derived",
        ),
    ),
    ("BSD-2-Clause", ("Redistribution and use in source and binary forms",)),
    ("MIT", ("Permission is hereby granted, free of charge",)),
    (
        "ISC",
        ("Permission to use, copy, modify, and/or distribute this software",),
    ),
    (
        "Unlicense",
        ("This is free and unencumbered software released into the public domain",),
    ),
)


def _classify_license_text(text: str) -> str:
    """Best-effort SPDX id from raw LICENSE file text (offline heuristics)."""
    for spdx_id, needles in _LICENSE_TEXT_SIGNATURES:
        if all(n in text for n in needles):
            return spdx_id
    return UNKNOWN_LICENSE


def _go_escape(s: str) -> str:
    """Go module-cache path escaping: uppercase → ``!`` + lowercase, ``!`` → ``!!``.

    Mirrors ``golang.org/x/mod/module.EscapePath``: module paths and versions
    with uppercase letters (e.g. ``github.com/BurntSushi/toml``) are stored
    on disk case-escaped (``github.com/!burnt!sushi/toml``) so the cache is
    usable on case-insensitive filesystems.
    """
    out: list[str] = []
    for ch in s:
        if ch == "!":
            out.append("!!")
        elif ch.isupper():
            out.append("!" + ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _default_gomodcache() -> Path:
    env = os.environ.get("GOMODCACHE", "").strip()
    if env:
        return Path(env)
    gopath = os.environ.get("GOPATH", "").strip()
    if gopath:
        return Path(gopath) / "pkg" / "mod"
    return Path.home() / "go" / "pkg" / "mod"


def resolve_go_license(
    module: str, version: str, gomodcache: Path | None = None
) -> str:
    """Classify a Go module's license from its LICENSE* file in GOMODCACHE.

    Offline/local-only: modules not present in the cache (never ``go build``/
    ``go mod download``-ed locally) resolve to :data:`UNKNOWN_LICENSE`.
    """
    cache = gomodcache if gomodcache is not None else _default_gomodcache()
    mod_dir = cache / f"{_go_escape(module)}@{_go_escape(version)}"
    if not mod_dir.is_dir():
        return UNKNOWN_LICENSE
    for fname in _GO_LICENSE_FILENAMES:
        candidate = mod_dir / fname
        if candidate.is_file():
            with contextlib.suppress(OSError):
                return _classify_license_text(
                    candidate.read_text(encoding="utf-8", errors="replace")
                )
    return UNKNOWN_LICENSE


def _iter_go_sum_entries(p: Path) -> set[tuple[str, str]]:
    """go.sum → set of (module, version); ``/go.mod`` annotation lines collapse
    onto the same version as their module's regular entry."""
    out: set[tuple[str, str]] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            mod = parts[0]
            ver = parts[1].split("/")[0]
            out.add((mod, ver))
    return out


def scan_go_licenses(
    root: Path, *, gomodcache: Path | None = None
) -> list[PackageLicense]:
    """Return PackageLicense entries for go.sum modules, classified via GOMODCACHE."""
    results: list[PackageLicense] = []
    cache = gomodcache if gomodcache is not None else _default_gomodcache()
    seen: set[tuple[str, str]] = set()
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "go.sum" not in filenames:
            continue
        for mod, ver in _iter_go_sum_entries(Path(dirpath_str) / "go.sum"):
            if (mod, ver) in seen:
                continue
            seen.add((mod, ver))
            lic = resolve_go_license(mod, ver, gomodcache=cache)
            results.append(
                PackageLicense(
                    name=mod,
                    version=ver,
                    license_id=lic,
                    ecosystem="go",
                    source="go.sum+GOMODCACHE" if lic != UNKNOWN_LICENSE else "go.sum",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Rust: license field via `cargo metadata` (offline; graceful skip)
# ---------------------------------------------------------------------------


def _run_cargo_metadata(manifest_dir: Path) -> dict[str, object] | None:
    """Run ``cargo metadata --offline`` in *manifest_dir*; None on any failure.

    Graceful skip covers: no local ``cargo`` binary, non-zero exit (e.g.
    dependencies not yet fetched so ``--offline`` cannot resolve them), a
    timeout, or unparsable output.
    """
    if shutil.which("cargo") is None:
        return None
    try:
        proc = subprocess.run(
            ["cargo", "metadata", "--format-version", "1", "--offline"],
            cwd=manifest_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def scan_rust_licenses(root: Path) -> list[PackageLicense]:
    """Return PackageLicense entries for Cargo.lock crates via `cargo metadata`.

    Requires a local ``cargo`` binary and an already-fetched dependency cache
    (``--offline``); skips gracefully (no results for that tree) when either
    is unavailable. Path/workspace-local crates (``source`` is null) are
    excluded — they are the repo's own manifests, not locked dependencies.
    """
    results: list[PackageLicense] = []
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "Cargo.lock" not in filenames or "Cargo.toml" not in filenames:
            continue
        data = _run_cargo_metadata(Path(dirpath_str))
        if not data:
            continue
        packages = data.get("packages")
        if not isinstance(packages, list):
            continue
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            if pkg.get("source") is None:
                continue  # path/workspace-local crate, not a locked dependency
            name = pkg.get("name")
            ver = pkg.get("version")
            if not isinstance(name, str) or not name or not isinstance(ver, str):
                continue
            lic = pkg.get("license")
            lic_id = lic.strip() if isinstance(lic, str) and lic.strip() else None
            results.append(
                PackageLicense(
                    name=name,
                    version=ver,
                    license_id=lic_id or UNKNOWN_LICENSE,
                    ecosystem="rust",
                    source="cargo-metadata",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Deno: npm: specifiers resolved via DENO_DIR's npm registry cache (offline)
# ---------------------------------------------------------------------------


def _default_deno_dir() -> Path:
    env = os.environ.get("DENO_DIR", "").strip()
    if env:
        return Path(env)
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "deno"


def _split_npm_specifier(entry: str) -> tuple[str, str]:
    """Split "name@version" or "@scope/name@version" into (name, version)."""
    if entry.startswith("@"):
        second_at = entry.find("@", 1)
        if second_at == -1:
            return entry, ""
        return entry[:second_at], entry[second_at + 1 :]
    at = entry.find("@")
    if at == -1:
        return entry, ""
    return entry[:at], entry[at + 1 :]


def resolve_deno_npm_license(
    pkg_name: str, version: str, deno_dir: Path | None = None
) -> str:
    """Read the ``license`` field of a cached npm ``package.json`` (offline).

    Looks under ``DENO_DIR/npm/registry.npmjs.org/<pkg>/<version>/package.json``
    (Deno's local npm-compat cache layout). Missing/unresolvable → UNKNOWN.
    """
    cache = deno_dir if deno_dir is not None else _default_deno_dir()
    pkg_json = (
        cache / "npm" / "registry.npmjs.org" / pkg_name / version / "package.json"
    )
    if not pkg_json.is_file():
        return UNKNOWN_LICENSE
    with contextlib.suppress(Exception):
        data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
        raw_lic = data.get("license") or UNKNOWN_LICENSE
        if isinstance(raw_lic, dict):
            raw_lic = raw_lic.get("type") or UNKNOWN_LICENSE
        return str(raw_lic).strip() or UNKNOWN_LICENSE
    return UNKNOWN_LICENSE


def scan_deno_licenses(
    root: Path, *, deno_dir: Path | None = None
) -> list[PackageLicense]:
    """Return PackageLicense entries for deno.lock ``npm:`` specifiers.

    Only ``npm:`` specifiers are resolvable offline (via the cached
    package.json's ``license`` field); ``jsr:``/``http(s):`` specifiers have
    no local license source and are not reported (no manifest to read).
    """
    results: list[PackageLicense] = []
    cache = deno_dir if deno_dir is not None else _default_deno_dir()
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "deno.lock" not in filenames:
            continue
        for entry in versions_deno_lock(Path(dirpath_str) / "deno.lock"):
            if not entry.startswith("npm:"):
                continue
            name, ver = _split_npm_specifier(entry[len("npm:") :])
            if not name or not ver:
                continue
            lic = resolve_deno_npm_license(name, ver, deno_dir=cache)
            results.append(
                PackageLicense(
                    name=name,
                    version=ver,
                    license_id=lic,
                    ecosystem="deno",
                    source="deno.lock+DENO_DIR"
                    if lic != UNKNOWN_LICENSE
                    else "deno.lock",
                )
            )
    return results


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------

# Ecosystems with no local license extractor: their UNKNOWN entries are
# excluded from the --fail-unknown exit-2 gate (they would always be
# UNKNOWN — no offline metadata source exists — so failing on them is just
# noise). Currently: container images (no manifest to read offline).
NO_EXTRACTOR_ECOSYSTEMS = frozenset({"container"})


def _fail_gate_unknown(unknown: list[PackageLicense]) -> list[PackageLicense]:
    """Subset of *unknown* that should count toward --fail-unknown's exit 2."""
    return [p for p in unknown if p.ecosystem not in NO_EXTRACTOR_ECOSYSTEMS]


# Normalise SPDX-ish identifiers for comparison
_SPDX_NORM = re.compile(r"[^A-Za-z0-9]")


def _norm(lic: str) -> str:
    return _SPDX_NORM.sub("", lic).upper()


def _matches_any(lic_id: str, patterns: list[str]) -> bool:
    """True when *lic_id* matches any pattern (case-insensitive, punctuation-stripped)."""
    n = _norm(lic_id)
    return any(n == _norm(p) or _norm(p) in n for p in patterns)


# SPDX compound expression splitting: AND binds tighter than OR, so split on
# OR first (top level), then split each OR-branch on AND.
_SPDX_OR_SPLIT = re.compile(r"\s+OR\s+", re.IGNORECASE)
_SPDX_AND_SPLIT = re.compile(r"\s+AND\s+", re.IGNORECASE)


def _split_or(expr: str) -> list[str]:
    return _SPDX_OR_SPLIT.split(expr)


def _split_and(expr: str) -> list[str]:
    return _SPDX_AND_SPLIT.split(expr)


def _license_violates_deny(lic_id: str, deny: list[str]) -> bool:
    """True when a (possibly compound) SPDX *lic_id* violates the deny list.

    Compound SPDX expressions are parsed at a coarse level (``OR``/``AND``
    only; no ``WITH`` exception clauses or parenthesised grouping). An
    OR-expression grants a choice of licenses, so it only violates deny when
    *every* branch independently violates. An AND-branch requires all its
    parts simultaneously, so it violates deny when *any* part violates.
    """
    branches = _split_or(lic_id)
    return all(
        any(_matches_any(part.strip(), deny) for part in _split_and(branch))
        for branch in branches
    )


def _license_satisfies_allow(lic_id: str, allow: list[str]) -> bool:
    """True when a (possibly compound) SPDX *lic_id* is fully permitted by allow.

    An AND-branch is allowed only when all of its parts are allowed; an
    OR-expression is satisfied when at least one branch is fully allowed
    (the caller may pick that branch).
    """
    branches = _split_or(lic_id)
    return any(
        all(_matches_any(part.strip(), allow) for part in _split_and(branch))
        for branch in branches
    )


def apply_policy(
    packages: list[PackageLicense],
    *,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> PolicyResult:
    """Evaluate *packages* against the allow/deny lists.

    Policy rules:
    - ``deny`` list only: flag any package whose license matches the deny list.
    - ``allow`` list only: flag any package whose license is NOT in the allow list.
    - Both lists: deny list takes precedence (deny overrides allow).
    - Neither list: all non-UNKNOWN packages are "allowed"; UNKNOWN are collected.

    ``UNKNOWN`` licenses are always surfaced in ``PolicyResult.unknown``
    regardless of the policy mode.
    """
    result = PolicyResult()

    for pkg in packages:
        if pkg.license_id == UNKNOWN_LICENSE:
            result.unknown.append(pkg)
            continue

        if deny and _license_violates_deny(pkg.license_id, deny):
            result.violations.append(pkg)
            continue

        if allow and not _license_satisfies_allow(pkg.license_id, allow):
            result.violations.append(pkg)
            continue

        result.allowed.append(pkg)

    result.policy_mode = "deny" if (deny and not allow) else "allow"
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_LICENSE_EPILOG = """\
exit codes:
  0  no violations and no unknown licenses (or --no-fail-unknown)
  1  policy violations found (denied / not in allow-list)
  2  unknown licenses found in an ecosystem with a local extractor
     (and --no-fail-unknown not set); container images never trigger this

scope (best-effort):
  npm: reads node_modules/**/package.json (requires prior npm/pnpm/yarn install).
  Python: reads pyproject.toml manifests in the tree, plus each uv.lock's
    locked third-party packages with licenses resolved from an adjacent
    venv's *.dist-info/METADATA (offline; UNKNOWN without installed metadata).
  Go: go.sum module@version pairs resolved via LICENSE* files in GOMODCACHE
    (offline; local module cache only, default ~/go/pkg/mod).
  Rust: Cargo.lock crates resolved via `cargo metadata --offline` (requires
    a local cargo and an already-fetched dependency cache).
  Deno: deno.lock npm: specifiers resolved via DENO_DIR's npm registry cache
    (offline); jsr:/http(s): specifiers have no local license source.
  container: no local license source; reported unknown but excluded from
    the --fail-unknown exit-2 bucket (see NO_EXTRACTOR_ECOSYSTEMS).

allow/deny list format:
  SPDX identifiers, comma-separated, case-insensitive.
  e.g. --allow MIT,Apache-2.0,BSD-2-Clause,ISC
       --deny GPL,AGPL,LGPL
"""


def build_license_parser() -> argparse.ArgumentParser:
    """Return the argument parser for 'repo-ops license'."""
    parser = argparse.ArgumentParser(
        prog="repo-ops license",
        description=(
            "Scan dependency licenses and flag violations against an "
            "allow/deny list (read-only; no lockfile writes)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_LICENSE_EPILOG,
    )
    add_roots_argument(parser)
    parser.add_argument(
        "--allow",
        default=None,
        metavar="LICENSES",
        help=(
            "Comma-separated SPDX license identifiers that are permitted. "
            "Packages with licenses NOT in this list are flagged as violations."
        ),
    )
    parser.add_argument(
        "--deny",
        default=None,
        metavar="LICENSES",
        help=(
            "Comma-separated SPDX license identifiers that are forbidden. "
            "Packages with licenses IN this list are flagged as violations."
        ),
    )
    parser.add_argument(
        "--no-fail-unknown",
        action="store_true",
        dest="no_fail_unknown",
        help="Exit 0 even when unknown licenses are found (default: exit 2).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Emit JSON instead of human-readable output.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="With --json: emit single-line JSON.",
    )
    add_verbose_argument(parser)
    return parser


def _parse_license_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def main(argv: list[str] | None = None) -> int:
    """Entry point for 'repo-ops license'."""
    parser = build_license_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)

    roots = resolve_scan_roots(list(args.roots), parser)
    allow_list = _parse_license_list(getattr(args, "allow", None))
    deny_list = _parse_license_list(getattr(args, "deny", None))

    all_packages: list[PackageLicense] = []
    for root_str in roots:
        root = Path(root_str)
        all_packages.extend(scan_npm_licenses(root))
        all_packages.extend(scan_python_licenses(root))
        all_packages.extend(scan_python_dep_licenses(root))
        all_packages.extend(scan_go_licenses(root))
        all_packages.extend(scan_rust_licenses(root))
        all_packages.extend(scan_deno_licenses(root))

    result = apply_policy(
        all_packages, allow=allow_list or None, deny=deny_list or None
    )

    if args.json_out:
        payload: dict[str, object] = {
            "schema_version": 1,
            "roots": roots,
            "policy": {
                "allow": allow_list,
                "deny": deny_list,
            },
            "violations": [
                {
                    "name": p.name,
                    "version": p.version,
                    "license": p.license_id,
                    "ecosystem": p.ecosystem,
                    "source": p.source,
                }
                for p in result.violations
            ],
            "unknown": [
                {
                    "name": p.name,
                    "version": p.version,
                    "ecosystem": p.ecosystem,
                    "source": p.source,
                }
                for p in result.unknown
            ],
            "summary": {
                "scanned": len(all_packages),
                "violations": len(result.violations),
                "unknown": len(result.unknown),
                "allowed": len(result.allowed),
            },
        }
        indent = None if args.compact else 2
        print(json.dumps(payload, indent=indent))
    else:
        total = len(all_packages)
        print(
            f"License scan: {total} package(s) across {len(roots)} root(s)  "
            f"[violations={len(result.violations)} unknown={len(result.unknown)}]"
        )
        if result.violations:
            print("\nViolations:")
            for p in sorted(result.violations, key=lambda x: x.name):
                print(f"  {p.name}@{p.version}  [{p.license_id}]  ({p.ecosystem})")
        if result.unknown:
            print(f"\nUnknown licenses ({len(result.unknown)} package(s)):")
            for p in sorted(result.unknown, key=lambda x: x.name)[:20]:
                print(f"  {p.name}@{p.version}  ({p.ecosystem}, {p.source})")
            if len(result.unknown) > 20:
                print(f"  ... and {len(result.unknown) - 20} more")
            print(
                "\nNote: license data is best-effort (npm: node_modules; "
                "Python: pyproject.toml + uv.lock/venv dist-info; "
                "Go: go.sum + GOMODCACHE LICENSE files; "
                "Rust: Cargo.lock + cargo metadata; "
                "Deno: deno.lock npm: specifiers + DENO_DIR cache; "
                "container images: unsupported, excluded from --fail-unknown)."
            )

    if result.violations:
        return 1
    if (
        result.unknown
        and not args.no_fail_unknown
        and _fail_gate_unknown(result.unknown)
    ):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
