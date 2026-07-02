"""License-policy gate: scan dependency licenses and report/flag violations.

Scope (first-cut, best-effort)
-------------------------------
- npm: reads package.json under node_modules/** (requires prior npm/yarn/pnpm
  install; no network calls).
- Python: reads the ``license`` field of each ``pyproject.toml`` found in the
  scanned tree — i.e. the repo's own package manifests, not its resolved
  dependency tree. This does NOT resolve installed/locked third-party
  dependency licenses (e.g. via a venv or ``uv.lock``); ``setup.cfg`` is not
  read.
- All other ecosystems: reported as UNKNOWN.

Limitations are documented in the output when unknown licenses are found.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
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
# Policy evaluation
# ---------------------------------------------------------------------------

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
  2  unknown licenses found (and --no-fail-unknown not set)

scope (best-effort first-cut):
  npm: reads node_modules/**/package.json (requires prior npm/pnpm/yarn install).
  Python: reads pyproject.toml files in the tree (direct-manifest license
    field only; does not resolve installed/locked dependency licenses).
  Go / Rust / Deno / container: not yet supported; reported as unknown.

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
                "Python: pyproject.toml; other ecosystems: unsupported)."
            )

    if result.violations:
        return 1
    if result.unknown and not args.no_fail_unknown:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
