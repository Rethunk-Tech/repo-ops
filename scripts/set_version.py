#!/usr/bin/env python3
"""Get, set, or bump the version across every workspace ``pyproject.toml``.

The root ``pyproject.toml`` and every ``packages/*/pyproject.toml`` carry an
independent ``[project].version`` that must move in lockstep on each release.
This keeps them in sync so a release no longer means hand-editing ~20 files.

Usage::

    python scripts/set_version.py                  # show current version(s)
    python scripts/set_version.py set 0.7.1        # set an explicit version
    python scripts/set_version.py bump patch       # bump (patch | minor | major)
    python scripts/set_version.py bump minor --dry-run

Exit status: 0 on success; 1 on version drift (in show mode), an invalid
version, no manifests found, or any manifest whose version cannot be parsed
(non-standard format such as pre/post-release).  Unparseable manifests are
never silently skipped — they are reported on stderr so mixed-version state
cannot be hidden.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Matches a `version = "X.Y.Z"` assignment, capturing the prefix, the semver,
# and the closing quote so surrounding formatting is preserved on rewrite.
VERSION_RE = re.compile(r'^(version\s*=\s*")(\d+\.\d+\.\d+)(")\s*$')
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
PARTS = ("major", "minor", "patch")


def manifests() -> list[Path]:
    """All workspace pyproject.toml files, root first then packages sorted."""
    paths = [ROOT / "pyproject.toml"]
    pkgs = ROOT / "packages"
    if pkgs.is_dir():
        paths += sorted(pkgs.glob("*/pyproject.toml"))
    return [p for p in paths if p.is_file()]


def _project_version_index(lines: list[str]) -> int | None:
    """Index of the version line inside the ``[project]`` table, else None.

    Scoped to ``[project]`` so a ``version`` key under ``[build-system]`` or a
    ``[tool.*]`` table is never rewritten.
    """
    in_project = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project and VERSION_RE.match(line.rstrip("\n")):
            return i
    return None


def read_version(path: Path) -> str | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = _project_version_index(lines)
    if idx is None:
        return None
    match = VERSION_RE.match(lines[idx])
    return match.group(2) if match else None


def write_version(path: Path, new: str) -> bool:
    """Set ``[project].version`` to ``new``. Returns True if the file changed."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = _project_version_index([line.rstrip("\n") for line in lines])
    if idx is None:
        return False
    match = VERSION_RE.match(lines[idx].rstrip("\n"))
    if match is None or match.group(2) == new:
        return False
    end = "\n" if lines[idx].endswith("\n") else ""
    lines[idx] = f"{match.group(1)}{new}{match.group(3)}{end}"
    path.write_text("".join(lines), encoding="utf-8")
    return True


def bump_version(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _current_versions(paths: list[Path]) -> dict[Path, str]:
    found = {p: read_version(p) for p in paths}
    for p, v in found.items():
        if v is None:
            print(
                f"warning: {p.relative_to(ROOT)}: version could not be parsed "
                "(non-standard format; skipped)",
                file=sys.stderr,
            )
    return {p: v for p, v in found.items() if v is not None}


def _apply(paths: list[Path], new: str, dry_run: bool) -> int:
    if not SEMVER_RE.match(new):
        print(f"error: {new!r} is not a valid X.Y.Z version", file=sys.stderr)
        return 1
    changed = 0
    skipped = 0
    for path in paths:
        current = read_version(path)
        if current is None:
            rel = path.relative_to(ROOT)
            print(
                f"warning: {rel}: version could not be parsed "
                "(non-standard format; skipped)",
                file=sys.stderr,
            )
            skipped += 1
            continue
        rel = path.relative_to(ROOT)
        if current == new:
            continue
        if dry_run:
            print(f"  would set {rel}: {current} -> {new}")
        else:
            write_version(path, new)
            print(f"  {rel}: {current} -> {new}")
        changed += 1
    verb = "would update" if dry_run else "updated"
    print(f"{verb} {changed} manifest(s) to {new}")
    if skipped:
        print(
            f"warning: {skipped} manifest(s) skipped (unparseable version)",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p_set = sub.add_parser("set", help="set an explicit X.Y.Z version")
    p_set.add_argument("version")
    p_set.add_argument("--dry-run", action="store_true")
    p_bump = sub.add_parser("bump", help="increment major, minor, or patch")
    p_bump.add_argument("part", choices=PARTS)
    p_bump.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    paths = manifests()
    if not paths:
        print("error: no pyproject.toml manifests found", file=sys.stderr)
        return 1
    versions = _current_versions(paths)
    distinct = sorted(set(versions.values()))

    if args.command is None:
        for path, version in versions.items():
            print(f"  {path.relative_to(ROOT)}: {version}")
        if len(distinct) > 1:
            print(f"version drift: {', '.join(distinct)}", file=sys.stderr)
            return 1
        print(f"current version: {distinct[0] if distinct else '(none)'}")
        return 0

    if len(distinct) > 1:
        print(
            f"warning: manifests disagree ({', '.join(distinct)}); "
            "all will be set to the target",
            file=sys.stderr,
        )

    if args.command == "set":
        return _apply(paths, args.version, args.dry_run)

    # bump: derive from the root manifest as the source of truth.
    root_version = read_version(ROOT / "pyproject.toml")
    if root_version is None:
        print("error: root pyproject.toml has no [project].version", file=sys.stderr)
        return 1
    return _apply(paths, bump_version(root_version, args.part), args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
