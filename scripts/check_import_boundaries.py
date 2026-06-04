#!/usr/bin/env python3
"""Fail if workspace packages violate import boundaries (greenfield split)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"

FORBIDDEN: dict[str, set[str]] = {
    "repo_ops_lockfiles": {
        "repo_ops_upgrade",
        "repo_ops_inventory",
        "repo_ops_js",
        "repo_ops_js_npm",
        "repo_ops_js_pnpm",
        "repo_ops_js_bun",
        "repo_ops_js_yarn",
        "repo_ops_go",
        "repo_ops_rust",
        "repo_ops_py",
        "repo_ops_py_uv",
        "repo_ops_py_poetry",
        "repo_ops_py_pip_tools",
        "repo_ops_py_hatch",
    },
}

PM_PREFIXES = (
    "repo_ops_js_npm",
    "repo_ops_js_pnpm",
    "repo_ops_js_bun",
    "repo_ops_js_yarn",
    "repo_ops_py_uv",
    "repo_ops_py_poetry",
    "repo_ops_py_pip_tools",
    "repo_ops_py_hatch",
    "repo_ops_go",
    "repo_ops_rust",
)

ALLOWED_UPGRADE_SUBMODULES = frozenset({"registry", "constants"})


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


def _imports_in_file(path: Path) -> tuple[set[str], set[str]]:
    """Return (top-level modules, upgrade submodules e.g. registry)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tops: set[str] = set()
    upgrade_subs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(_top_level(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            tops.add(_top_level(mod))
            if mod == "repo_ops_upgrade":
                for alias in node.names:
                    upgrade_subs.add(alias.name)
            elif mod.startswith("repo_ops_upgrade."):
                upgrade_subs.add(mod.split(".", 1)[1])
    return tops, upgrade_subs


def main() -> int:
    errors: list[str] = []
    for pkg_dir in sorted(PACKAGES.iterdir()):
        if not pkg_dir.is_dir():
            continue
        src = pkg_dir / "src"
        if not src.exists():
            continue
        for py in src.rglob("*.py"):
            mod_root = py.relative_to(src).parts[0]
            imports, upgrade_subs = _imports_in_file(py)
            if mod_root in FORBIDDEN:
                for bad in imports & FORBIDDEN[mod_root]:
                    errors.append(f"{py}: must not import {bad}")
            if mod_root in PM_PREFIXES:
                for other in PM_PREFIXES:
                    if other != mod_root and other in imports:
                        errors.append(f"{py}: PM package must not import {other}")
                disallowed = upgrade_subs - ALLOWED_UPGRADE_SUBMODULES
                if disallowed:
                    errors.append(
                        f"{py}: PM package may only use repo_ops_upgrade."
                        f"{', '.join(sorted(ALLOWED_UPGRADE_SUBMODULES))} "
                        f"(found {sorted(disallowed)})"
                    )
    if errors:
        print("Import boundary violations:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("Import boundaries: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
