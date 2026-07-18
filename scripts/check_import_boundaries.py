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
        "repo_ops_py_pdm",
        "repo_ops_py_pipenv",
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
    "repo_ops_py_pdm",
    "repo_ops_py_pipenv",
    "repo_ops_go",
    "repo_ops_rust",
    "repo_ops_container",
)

ALLOWED_UPGRADE_SUBMODULES = frozenset({"registry", "constants"})

# Packages allowed to reach a narrow set of repo_ops_upgrade submodules
# instead of the blanket FORBIDDEN ban (e.g. sbom reuses the Actions
# `uses:` pin parser rather than duplicating it).
ALLOWED_UPGRADE_SUBMODULES_BY_PKG: dict[str, frozenset[str]] = {
    "repo_ops_lockfiles": frozenset({"workflow_uses"}),
    "repo_ops_audit": frozenset({"registry", "orchestration"}),
}

REMEDIATIONS_PREFIXES = (
    "repo_ops_upgrade_remediations_core",
    "repo_ops_upgrade_remediations_js",
    "repo_ops_upgrade_remediations_go",
    "repo_ops_upgrade_remediations_py",
    "repo_ops_upgrade_remediations_rust",
    "repo_ops_upgrade_remediations_deno",
)

REMEDIATIONS_BASE_ALLOWED = frozenset(
    {
        "repo_ops_core",
        "repo_ops_models",
    }
)

REMEDIATIONS_EXTRA_ALLOWED: dict[str, frozenset[str]] = {
    "repo_ops_upgrade_remediations_core": frozenset(),
    "repo_ops_upgrade_remediations_js": frozenset(
        {
            "repo_ops_js",
            "repo_ops_lockfiles",
            "repo_ops_upgrade_remediations_core",
        }
    ),
    "repo_ops_upgrade_remediations_go": frozenset(
        {"repo_ops_upgrade_remediations_core"}
    ),
    "repo_ops_upgrade_remediations_py": frozenset(
        {"repo_ops_upgrade_remediations_core"}
    ),
    "repo_ops_upgrade_remediations_rust": frozenset(
        {"repo_ops_upgrade_remediations_core"}
    ),
    "repo_ops_upgrade_remediations_deno": frozenset(
        {"repo_ops_upgrade_remediations_core"}
    ),
}

REMEDIATIONS_UPGRADE_SUBMODULES: dict[str, frozenset[str]] = {
    "repo_ops_upgrade_remediations_js": frozenset({"constants"}),
}

APP_PKG_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "repo_ops_audit": frozenset(
        {
            "repo_ops_core",
            "repo_ops_models",
            "repo_ops_js_npm",
            "repo_ops_js_yarn",
            "repo_ops_js_pnpm",
            "repo_ops_js_bun",
            "repo_ops_py_uv",
            "repo_ops_rust",
            "repo_ops_go",
        }
    ),
    "repo_ops_dependabot": frozenset(
        {
            "repo_ops_core",
            "repo_ops_lockfiles",
        }
    ),
    "repo_ops_doctor": frozenset({"repo_ops_core"}),
}


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


def _workspace_imports(imports: set[str]) -> set[str]:
    """Top-level workspace modules only (repo_ops_*)."""
    return {name for name in imports if name.startswith("repo_ops_")}


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


def _check_remediations(
    mod_root: str,
    imports: set[str],
    upgrade_subs: set[str],
    py: Path,
    errors: list[str],
) -> None:
    allowed = (
        REMEDIATIONS_BASE_ALLOWED
        | REMEDIATIONS_EXTRA_ALLOWED.get(mod_root, frozenset())
        | {mod_root}
    )
    disallowed = _workspace_imports(imports) - allowed - {"repo_ops_upgrade"}
    if disallowed:
        for bad in sorted(disallowed):
            errors.append(f"{py}: remediations package must not import {bad}")
    if "repo_ops_upgrade" in imports:
        allowed_subs = REMEDIATIONS_UPGRADE_SUBMODULES.get(mod_root)
        if allowed_subs is None:
            found = sorted(upgrade_subs) if upgrade_subs else "bare import"
            errors.append(f"{py}: remediations package must not import repo_ops_upgrade ({found})")
        else:
            bad_subs = upgrade_subs - allowed_subs
            if bad_subs or not upgrade_subs:
                found = sorted(bad_subs) if bad_subs else "bare import"
                errors.append(
                    f"{py}: may only use repo_ops_upgrade."
                    f"{', '.join(sorted(allowed_subs))} (found {found})"
                )


def _check_app_pkg(
    mod_root: str,
    imports: set[str],
    upgrade_subs: set[str],
    py: Path,
    errors: list[str],
) -> None:
    allowed = APP_PKG_ALLOWED_IMPORTS[mod_root] | {mod_root}
    disallowed = _workspace_imports(imports) - allowed - {"repo_ops_upgrade"}
    if disallowed:
        for bad in sorted(disallowed):
            errors.append(f"{py}: {mod_root} must not import {bad}")
    narrow = ALLOWED_UPGRADE_SUBMODULES_BY_PKG.get(mod_root)
    if narrow is not None and "repo_ops_upgrade" in imports:
        bad_subs = upgrade_subs - narrow
        if bad_subs or not upgrade_subs:
            found = sorted(bad_subs) if bad_subs else "bare import"
            errors.append(
                f"{py}: may only use repo_ops_upgrade."
                f"{', '.join(sorted(narrow))} (found {found})"
            )


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
            narrow = ALLOWED_UPGRADE_SUBMODULES_BY_PKG.get(mod_root)
            if (
                narrow is not None
                and mod_root not in APP_PKG_ALLOWED_IMPORTS
                and "repo_ops_upgrade" in imports
            ):
                disallowed = upgrade_subs - narrow
                if disallowed or not upgrade_subs:
                    found = sorted(disallowed) if disallowed else "bare import"
                    errors.append(
                        f"{py}: may only use repo_ops_upgrade."
                        f"{', '.join(sorted(narrow))} (found {found})"
                    )
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
            if mod_root in REMEDIATIONS_PREFIXES:
                _check_remediations(mod_root, imports, upgrade_subs, py, errors)
            if mod_root in APP_PKG_ALLOWED_IMPORTS:
                _check_app_pkg(mod_root, imports, upgrade_subs, py, errors)
    if errors:
        print("Import boundary violations:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("Import boundaries: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
