"""Post-upgrade remediation hooks (biome, playwright, codegen, pm pin)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from repo_ops_core.constants import resolve_timeout_apply
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run
from repo_ops_core.version_utils import normalize_version, semver_tuple
from repo_ops_js.workspaces import (
    parse_package_manager,
    pm_exec_prefix,
    pm_run_prefix,
    pm_version_argv,
    yarn_workspace_members,
)
from repo_ops_lockfiles.snapshots import (
    resolved_version_from_node_modules,
)
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade.constants import BIOME_PKG, CODEGEN_TRIGGERS, PLAYWRIGHT_PKG
from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    register,
    run_hooks,
    stderr_tail,
)


def _find_biome_configs(mdir: Path) -> list[Path]:
    configs: list[Path] = []
    for name in ("biome.json", "biome.jsonc"):
        p = mdir / name
        if p.exists():
            configs.append(p)
    for pattern in yarn_workspace_members(mdir) or []:
        if "*" not in pattern:
            sub = mdir / pattern
            for name in ("biome.json", "biome.jsonc"):
                if (sub / name).exists():
                    configs.append(sub / name)
        else:
            # Expand glob pattern (e.g. "packages/*") bounded to workspace root
            for sub in mdir.glob(pattern):
                if sub.is_dir():
                    for name in ("biome.json", "biome.jsonc"):
                        if (sub / name).exists():
                            configs.append(sub / name)
    return configs


def _biome_installed_version(mdir: Path) -> str | None:
    resolved = resolved_version_from_node_modules(mdir, BIOME_PKG)
    if resolved:
        return resolved
    pkg = read_json_file(mdir / "package.json") or {}
    for section in ("dependencies", "devDependencies"):
        ver = (pkg.get(section) or {}).get(BIOME_PKG)
        if ver:
            return normalize_version(str(ver))
    return None


def _schema_version_from_config(p: Path) -> str | None:
    d = read_json_file(p)
    if not d:
        return None
    schema = d.get("$schema", "")
    m = re.search(r"/schemas/([\d.]+)/", str(schema))
    return m.group(1) if m else None


def detect_biome(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node":
        return False
    if not _find_biome_configs(ws.path):
        return False
    outdated = {c.name for c in ctx.get("changes", [])}
    if BIOME_PKG in outdated or "biome" in outdated:
        return True
    for cfg in _find_biome_configs(ws.path):
        inst = _biome_installed_version(ws.path)
        sch = _schema_version_from_config(cfg)
        if inst and sch and semver_tuple(inst) != semver_tuple(sch):
            return True
    return False


def report_biome(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    inst = _biome_installed_version(ws.path) or "?"
    for cfg in _find_biome_configs(ws.path):
        sch = _schema_version_from_config(cfg) or "?"
        if semver_tuple(inst) != semver_tuple(sch):
            lines.append(
                f"REMEDIATE biome: {cfg.name} $schema {sch} → {inst} "
                f"(would run biome migrate --write)"
            )
    return lines


def apply_biome(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    prefix = pm_exec_prefix(ws.manager)
    ok = True
    last_err = ""
    for cfg in _find_biome_configs(ws.path):
        cfg_dir = cfg.parent
        cmd = [*prefix, "biome", "migrate", "--write"]
        if cfg_dir != ws.path:
            cmd.append(f"--config-path={cfg_dir}")
        proc = run(cmd, ws.path, timeout=resolve_timeout_apply())
        if proc.returncode != 0:
            ok = False
            last_err = stderr_tail(proc.stderr or proc.stdout or "")
    if ok:
        return True, "biome migrate --write (ok)"
    return False, f"biome migrate --write (failed: {last_err})"


def _playwright_version_changed(ws: Workspace, ctx: dict[str, Any]) -> bool:
    for c in ctx.get("changes", []):
        if c.name == PLAYWRIGHT_PKG or PLAYWRIGHT_PKG in c.name:
            if c.from_ver not in ("?", "changed", "prior") and c.to not in (
                "?",
                "changed",
                "updated",
            ):
                return normalize_version(c.from_ver) != normalize_version(c.to)
            return True
    return False


def _playwright_missing_browsers(ws: Workspace) -> bool:
    prefix = pm_exec_prefix(ws.manager)
    proc = run(
        [*prefix, "playwright", "install", "--dry-run"],
        ws.path,
        timeout=120,
    )
    if proc.returncode != 0:
        return False
    combined = (proc.stdout + proc.stderr).lower()
    return any(
        tok in combined
        for tok in (
            "download",
            "installing",
            "missing",
            "will download",
            "needs install",
        )
    )


def detect_playwright(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node":
        return False
    pkg = read_json_file(ws.path / "package.json") or {}
    deps = {
        **(pkg.get("dependencies") or {}),
        **(pkg.get("devDependencies") or {}),
        **(pkg.get("peerDependencies") or {}),
        **(pkg.get("optionalDependencies") or {}),
    }
    if PLAYWRIGHT_PKG not in deps:
        return False
    outdated = {c.name for c in ctx.get("changes", [])}
    if PLAYWRIGHT_PKG in outdated or any(PLAYWRIGHT_PKG in n for n in outdated):
        return True
    if _playwright_version_changed(ws, ctx):
        return True
    # Browser dry-run shells out (network-capable). Gate to apply mode so
    # plan/detect never triggers a PM subprocess just to probe browsers.
    if not ctx.get("apply", True):
        return False
    return _playwright_missing_browsers(ws)


def report_playwright(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [f"REMEDIATE playwright: would run {ws.manager} exec playwright install"]


def apply_playwright(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    prefix = pm_exec_prefix(ws.manager)
    proc = run(
        [*prefix, "playwright", "install"], ws.path, timeout=resolve_timeout_apply()
    )
    cmd = f"{ws.manager} exec playwright install"
    if proc.returncode == 0:
        return True, f"{cmd} (ok)"
    err = stderr_tail(proc.stderr or proc.stdout or "")
    return False, f"{cmd} (failed: {err})"


def detect_codegen(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node":
        return False
    pkg = read_json_file(ws.path / "package.json") or {}
    scripts = pkg.get("scripts") or {}
    if not any(k in scripts for k in ("generate:proto", "generate:openapi")):
        return False
    outdated = {c.name for c in ctx.get("changes", [])}
    return any(any(t in n for t in CODEGEN_TRIGGERS) for n in outdated)


def report_codegen(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    pkg = read_json_file(ws.path / "package.json") or {}
    scripts = pkg.get("scripts") or {}
    lines: list[str] = []
    for script in ("generate:proto", "generate:openapi"):
        if script in scripts:
            lines.append(f"REMEDIATE codegen: would run {ws.manager} run {script}")
    return lines


def apply_codegen(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    prefix = pm_run_prefix(ws.manager)
    ok = True
    last_err = ""
    pkg = read_json_file(ws.path / "package.json") or {}
    for script in ("generate:proto", "generate:openapi"):
        if script in (pkg.get("scripts") or {}):
            proc = run([*prefix, script], ws.path, timeout=resolve_timeout_apply())
            if proc.returncode != 0:
                ok = False
                last_err = stderr_tail(proc.stderr or proc.stdout or "")
    if ok:
        return True, f"{ws.manager} run codegen (ok)"
    return False, f"{ws.manager} run codegen (failed: {last_err})"


def _pm_runtime_version(name: str, ws: Workspace) -> str | None:
    argv = pm_version_argv(name)
    if not argv:
        return None
    proc = run(argv, ws.path, timeout=30)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().lstrip("v")


def detect_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node":
        return False
    pkg = read_json_file(ws.path / "package.json") or {}
    pm_field = parse_package_manager(pkg)
    if not pm_field:
        return False
    name, pinned = pm_field
    actual = _pm_runtime_version(name, ws)
    if actual is None:
        return False
    return normalize_version(pinned) != normalize_version(actual)


def report_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    pkg = read_json_file(ws.path / "package.json") or {}
    pm_field = parse_package_manager(pkg)
    if not pm_field:
        return []
    name, _pinned = pm_field
    pm = (pkg.get("packageManager") or "").strip()
    actual = _pm_runtime_version(name, ws) or "?"
    return [f"REMEDIATE package_manager_pin: {pm} → {name}@{actual}"]


def apply_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    pkg_path = ws.path / "package.json"
    pkg = read_json_file(pkg_path)
    if not pkg:
        return False, "packageManager pin (failed: missing package.json)"
    pm_field = parse_package_manager(pkg)
    if not pm_field:
        return False, "packageManager pin (failed: no packageManager field)"
    name, _ = pm_field
    actual = _pm_runtime_version(name, ws)
    if actual is None:
        return False, f"packageManager pin (failed: {name} --version)"
    pkg["packageManager"] = f"{name}@{actual}"
    pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    return True, f"packageManager → {name}@{actual} (ok)"


def _biome_files(ws: Workspace) -> list[str]:
    return [str(p.relative_to(ws.path)) for p in _find_biome_configs(ws.path)]


REMEDIATIONS: list[RemediationHook] = [
    RemediationHook(
        "biome_schema",
        detect_biome,
        report_biome,
        apply_biome,
        files=_biome_files,
    ),
    RemediationHook(
        "playwright_browsers", detect_playwright, report_playwright, apply_playwright
    ),
    RemediationHook("codegen", detect_codegen, report_codegen, apply_codegen),
    RemediationHook(
        "package_manager_pin",
        detect_pm_pin,
        report_pm_pin,
        apply_pm_pin,
        files=lambda ws: ["package.json"],
    ),
]

# Register all JS hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


def run_remediations(
    result: WorkspaceResult,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    """Run JS remediation hooks against *result*.

    Delegates to :func:`repo_ops_upgrade_remediations_core.remediations.run_hooks`
    using the local ``REMEDIATIONS`` list.  Patching ``REMEDIATIONS`` in tests
    overrides which hooks run without affecting the global registry.
    """
    run_hooks(REMEDIATIONS, result, apply=apply, skip=skip)
