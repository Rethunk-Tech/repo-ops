"""Post-upgrade remediation hooks (biome, playwright, codegen, pm pin)."""

from __future__ import annotations

import functools
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import guard_tree_writes, guarded_write, is_under_root, read_json_file
from repo_ops_core.http import open_url
from repo_ops_core.process import run
from repo_ops_core.version_utils import normalize_version, semver_tuple
from repo_ops_core.walk import iter_scanned_dirs
from repo_ops_js.deno import _strip_deno_jsonc_comments
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
from repo_ops_models import Workspace
from repo_ops_upgrade.actions import discover_workflow_files
from repo_ops_upgrade.constants import BIOME_PKG, CODEGEN_TRIGGERS, PLAYWRIGHT_PKG
from repo_ops_upgrade_remediations_core.remediations import (
    ApplyResult,
    RemediationHook,
    make_run_remediations,
    register,
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
    if p.suffix == ".jsonc":
        try:
            d = json.loads(_strip_deno_jsonc_comments(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
    else:
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
    leftover = False
    if not guard_tree_writes(ws.git_repo, "biome", ws.path):
        return True, "biome (skipped: a link resolves outside the repo)"
    for cfg in _find_biome_configs(ws.path):
        cfg_dir = cfg.parent
        scope = [] if cfg_dir == ws.path else [f"--config-path={cfg_dir}"]
        proc = run(
            [*prefix, "biome", "migrate", "--write", *scope],
            ws.path,
            timeout=TIMEOUT_APPLY,
            scrub_auth=True,
        )
        if proc.returncode != 0:
            ok = False
            last_err = stderr_tail(proc.stderr or proc.stdout or "")
            continue
        # Safe fixes only, never --unsafe. Exit 1 means diagnostics remain, which
        # verify judges; the edits are owned like any other this run wrote.
        check = run(
            [*prefix, "biome", "check", "--write", *scope],
            ws.path,
            timeout=TIMEOUT_APPLY,
            scrub_auth=True,
        )
        leftover = leftover or check.returncode != 0
    if ok:
        suffix = "; diagnostics remain after safe fixes" if leftover else ""
        return True, f"biome migrate --write, biome check --write (ok{suffix})"
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


_INSTALL_LOCATION = re.compile(r"^\s*Install location:\s*(\S.*?)\s*$", re.MULTILINE)

# Browsers live in one machine-wide cache keyed by playwright version, so the
# answer for a version is the answer for every workspace pinning it. Without
# this the fleet paid one probe and one install per repo for the same bytes.
_BROWSER_PROBE: dict[str, bool] = {}
_BROWSER_PROBE_LOCK = threading.Lock()


def _playwright_missing_browsers(ws: Workspace, version: str) -> bool:
    with _BROWSER_PROBE_LOCK:
        if version in _BROWSER_PROBE:
            return _BROWSER_PROBE[version]
    prefix = pm_exec_prefix(ws.manager)
    proc = run(
        [*prefix, "playwright", "install", "--dry-run"],
        ws.path,
        timeout=120,
        scrub_auth=True,
    )
    # A dry-run prints an "Install location:" per browser whether or not it is
    # there, and a "Download url:" beside it either way, so the paths are the
    # only thing that says what is actually on disk.
    locations = _INSTALL_LOCATION.findall(proc.stdout + proc.stderr)
    missing = (
        False
        if proc.returncode != 0 or not locations
        else any(not Path(loc).exists() for loc in locations)
    )
    with _BROWSER_PROBE_LOCK:
        _BROWSER_PROBE[version] = missing
    return missing


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
    return _playwright_missing_browsers(ws, str(deps[PLAYWRIGHT_PKG]))


def report_playwright(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [f"REMEDIATE playwright: would run {ws.manager} exec playwright install"]


def apply_playwright(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    prefix = pm_exec_prefix(ws.manager)
    proc = run([*prefix, "playwright", "install"], ws.path, timeout=TIMEOUT_APPLY, scrub_auth=True)
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
    if not guard_tree_writes(ws.git_repo, "codegen", ws.path):
        return True, "codegen (skipped: a link resolves outside the repo)"
    pkg = read_json_file(ws.path / "package.json") or {}
    for script in ("generate:proto", "generate:openapi"):
        if script in (pkg.get("scripts") or {}):
            proc = run([*prefix, script], ws.path, timeout=TIMEOUT_APPLY, scrub_auth=True)
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


# The npm package ``yarn`` is Yarn Classic, so a Berry pin would be walked
# onto the wrong line; yarn pins follow the runtime only.
_PM_REGISTRY_PACKAGES = frozenset({"bun", "npm", "pnpm"})


@functools.cache
def _pm_registry_latest(name: str) -> str | None:
    if name not in _PM_REGISTRY_PACKAGES:
        return None
    try:
        with open_url(f"https://registry.npmjs.org/{name}/latest", timeout=TIMEOUT_CHECK) as resp:
            version = json.load(resp).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def _pm_target_version(name: str, ws: Workspace) -> str | None:
    """Newest of the local runtime and the registry's latest release.

    A host whose manager lags the registry would otherwise hold the pin back.
    """
    found = [v for v in (_pm_runtime_version(name, ws), _pm_registry_latest(name)) if v]
    return max(found, key=semver_tuple, default=None)


_BUN_UPGRADE_LOCK = threading.Lock()
_BUN_UPGRADE_DONE = False


def _bun_upgrade(ws: Workspace, target: str) -> None:
    """Move the host's bun to *target* once per sweep, logging both versions.

    The pin is the version the fleet resolves against, so a host still on an
    older bun would otherwise install with a runtime nobody else runs. The
    once-per-sweep state is module-wide because a sweep visits every workspace
    from this one process.
    """
    global _BUN_UPGRADE_DONE
    with _BUN_UPGRADE_LOCK:
        if _BUN_UPGRADE_DONE:
            return
        _BUN_UPGRADE_DONE = True
    before = _pm_runtime_version("bun", ws)
    if before is None or semver_tuple(before) >= semver_tuple(target):
        return
    proc = run(["bun", "upgrade"], ws.path, timeout=TIMEOUT_APPLY)
    after = _pm_runtime_version("bun", ws) or "?"
    print(f"bun upgrade: {before} → {after}", file=sys.stderr, flush=True)
    if proc.returncode != 0:
        print(
            f"WARN bun upgrade failed: {stderr_tail(proc.stderr or proc.stdout or '')}",
            file=sys.stderr,
            flush=True,
        )


@functools.cache
def _bun_manifest_paths(root: Path) -> tuple[Path, ...]:
    """Every ``package.json`` path under *root* outside the pruned dirs.

    Cached because detect, report, apply and the hook's own ``files`` callback
    each ask. Paths only: the pins are re-read, so a manifest an earlier
    workspace repinned is never seen at its stale version.
    """
    return tuple(
        directory / "package.json"
        for directory, names in iter_scanned_dirs(root)
        if "package.json" in names
    )


def _bun_manifests(root: Path) -> list[tuple[Path, dict[str, Any], str]]:
    """Every ``package.json`` under *root* declaring a bun ``packageManager``.

    Nested manifests (fixtures, app dirs outside the declared workspaces) are
    not workspaces of their own, so nothing else in a sweep reaches their pin.
    """
    found: list[tuple[Path, dict[str, Any], str]] = []
    for path in _bun_manifest_paths(root):
        pkg = read_json_file(path)
        if not pkg:
            continue
        parsed = parse_package_manager(pkg)
        if parsed and parsed[0] == "bun":
            found.append((path, pkg, parsed[1]))
    return found


def _bun_pin_targets(ws: Workspace) -> list[tuple[Path, dict[str, Any], str, str]]:
    """``(manifest, pkg, pinned, latest)`` for every bun pin that trails the registry.

    Repo-wide rather than workspace-scoped: a fixture or app dir outside the
    declared workspaces still ships a pin the fleet resolves bun from.
    """
    manifests = _bun_manifests(ws.git_repo)
    if not manifests:
        return []
    target = _pm_target_version("bun", ws)
    if target is None:
        return []
    return [
        (path, pkg, pinned, target)
        for path, pkg, pinned in manifests
        if semver_tuple(pinned) < semver_tuple(target)
    ]


def _write_pm_pin(path: Path, pkg: dict[str, Any], name: str, version: str, root: Path) -> None:
    pkg["packageManager"] = f"{name}@{version}"
    guarded_write(
        path,
        (json.dumps(pkg, indent=2) + "\n").encode("utf-8"),
        root,
        appender="packageManager pin",
    )


def _detected_pm_targets(ws: Workspace) -> list[tuple[Path, str, str, str]]:
    """``(manifest, manager, pinned, target)`` for every pin this hook would move."""
    moves: list[tuple[Path, str, str, str]] = []
    pkg_path = ws.path / "package.json"
    pkg = read_json_file(pkg_path) or {}
    pm_field = parse_package_manager(pkg)
    if pm_field:
        name, pinned = pm_field
        target = _pm_target_version(name, ws)
        if target is not None and semver_tuple(target) > semver_tuple(pinned):
            moves.append((pkg_path, name, pinned, target))
    own = pkg_path.resolve()
    for path, _pkg, pinned, target in _bun_pin_targets(ws):
        if path.resolve() != own:
            moves.append((path, "bun", pinned, target))
    return moves


def detect_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node":
        return False
    return bool(_detected_pm_targets(ws))


def report_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [
        f"REMEDIATE package_manager_pin: {path.relative_to(ws.git_repo).as_posix()} "
        f"{name}@{pinned} → {name}@{target}"
        for path, name, pinned, target in _detected_pm_targets(ws)
    ]


def apply_pm_pin(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    pkg_path = ws.path / "package.json"
    if not is_under_root(ws.git_repo, pkg_path):
        return False, "packageManager pin (failed: package.json outside the repo)"
    pkg = read_json_file(pkg_path) or {}
    pm_field = parse_package_manager(pkg)
    name = pm_field[0] if pm_field else ("bun" if ws.ecosystem == "node" else None)
    if name is None:
        return False, "packageManager pin (failed: no packageManager field)"
    actual = _pm_target_version(name, ws)
    if actual is None:
        return False, f"packageManager pin (failed: {name} --version)"
    if name == "bun":
        _bun_upgrade(ws, actual)
    moved = 0
    if pm_field and semver_tuple(actual) > semver_tuple(pm_field[1]):
        _write_pm_pin(pkg_path, pkg, name, actual, ws.git_repo)
        moved += 1
    if name == "bun":
        own = pkg_path.resolve()
        for path, nested, _pinned, target in _bun_pin_targets(ws):
            if path.resolve() == own:
                continue
            _write_pm_pin(path, nested, name, target, ws.git_repo)
            moved += 1
    if not moved:
        # Only reachable past detect for a second workspace in the same repo,
        # whose sibling already repinned: nothing left to do, not a failure.
        return True, f"packageManager → {name}@{actual} (already current)"
    return True, f"packageManager → {name}@{actual} ({moved} manifest(s), ok)"


def _pm_pin_files(ws: Workspace) -> list[str]:
    """Manifests this pin owns, relative to the workspace (may reach the repo root).

    Every bun-pinned manifest, not just the ones trailing the latest: this runs
    after apply, when the pins it moved no longer trail anything. Commit staging
    intersects the list with the dirty set, so an untouched one is never staged.
    """
    paths = dict.fromkeys(
        [
            (ws.path / "package.json").resolve(),
            *(path.resolve() for path, _pkg, _pin in _bun_manifests(ws.git_repo)),
        ]
    )
    rel = (os.path.relpath(p, ws.path) for p in paths)
    return [r for r in rel if (ws.path / r).exists()]


# ``bun-version:`` takes one literal version; ``latest`` and ``${{ … }}``
# expressions are resolved by the workflow itself and never match this shape.
_BUN_VERSION_LINE_RE = re.compile(
    r"^(?P<head>[ \t]*(?:- )?bun-version:[ \t]*)"
    r"(?P<quote>[\"']?)(?P<version>\d+(?:\.\d+)*)(?P=quote)"
    r"(?P<tail>[ \t]*(?:#.*)?)$",
    re.MULTILINE,
)


def _bun_version_bumps(ws: Workspace) -> list[tuple[Path, str]]:
    """Workflow files with a literal ``bun-version:`` trailing the bun latest."""
    target = _pm_target_version("bun", ws)
    if target is None:
        return []
    bumps: list[tuple[Path, str]] = []
    for wf in discover_workflow_files(ws.git_repo):
        text = wf.read_text(encoding="utf-8", errors="replace")
        if any(
            semver_tuple(m.group("version")) < semver_tuple(target)
            for m in _BUN_VERSION_LINE_RE.finditer(text)
        ):
            bumps.append((wf, target))
    return bumps


def detect_bun_workflow(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node":
        return False
    return bool(_bun_version_bumps(ws))


def report_bun_workflow(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return [
        f"REMEDIATE bun_workflow_version: {wf.relative_to(ws.git_repo).as_posix()} "
        f"bun-version → {target}"
        for wf, target in _bun_version_bumps(ws)
    ]


def apply_bun_workflow(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    changed: list[str] = []
    target = ""
    for wf, target in _bun_version_bumps(ws):
        text = wf.read_text(encoding="utf-8", errors="replace")

        def _sub(m: re.Match[str], target: str = target) -> str:
            if semver_tuple(m.group("version")) >= semver_tuple(target):
                return m.group(0)
            return f"{m.group('head')}{m.group('quote')}{target}{m.group('quote')}{m.group('tail')}"

        updated = _BUN_VERSION_LINE_RE.sub(_sub, text)
        if updated != text:
            guarded_write(
                wf,
                updated.encode("utf-8"),
                ws.git_repo,
                appender="bun workflow version",
            )
            changed.append(wf.relative_to(ws.git_repo).as_posix())
    if not changed:
        return True, "bun-version workflow inputs (ok: nothing to bump)"
    return True, f"bun-version → {target} in {', '.join(changed)} (ok)"


def _bun_workflow_files(ws: Workspace) -> list[str]:
    """Workflows holding a literal ``bun-version:``, trailing or not (see _pm_pin_files)."""
    return [
        wf.relative_to(ws.git_repo).as_posix()
        for wf in discover_workflow_files(ws.git_repo)
        if _BUN_VERSION_LINE_RE.search(wf.read_text(encoding="utf-8", errors="replace"))
    ]


def _governed_by_biome(ws: Workspace) -> bool:
    for d in (ws.path, *ws.path.parents):
        if not d.is_relative_to(ws.git_repo):
            return False
        if any((d / name).exists() for name in ("biome.json", "biome.jsonc")):
            return True
    return False


def _biome_bin(ws: Workspace) -> Path:
    return ws.path / "node_modules" / ".bin" / "biome"


def _rewritten_manifests(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    snap: dict[Path, bytes] = ctx.get("pre_apply_snapshot") or {}
    return sorted(
        str(p.relative_to(ws.path))
        for p, before in snap.items()
        if p.name == "package.json" and p.exists() and p.read_bytes() != before
    )


def detect_manifest_format(ws: Workspace, ctx: dict[str, Any]) -> bool:
    if ws.ecosystem != "node" or not _governed_by_biome(ws) or not _biome_bin(ws).exists():
        return False
    if not ctx.get("apply", True):
        return bool(ctx.get("changes"))
    return bool(_rewritten_manifests(ws, ctx))


def report_manifest_format(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
    return ["REMEDIATE manifest_format: would run biome format --write on rewritten package.json"]


def apply_manifest_format(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
    """Restore the repo's Biome layout on manifests the package manager rewrote.

    bun, npm and pnpm re-serialise package.json with every object expanded, so a
    repo formatting JSON with ``expand: "never"`` fails lint on each one they write.
    """
    files = _rewritten_manifests(ws, ctx)
    # A manifest the repo's own biome config ignores is left as written, not a failure.
    cmd = [str(_biome_bin(ws)), "format", "--write", "--no-errors-on-unmatched", *files]
    proc = run(cmd, ws.path, timeout=TIMEOUT_APPLY, network=False, scrub_auth=True)
    detail = f"biome format --write {' '.join(files)}"
    if proc.returncode == 0:
        return True, f"{detail} (ok)"
    return False, f"{detail} (failed: {stderr_tail(proc.stderr or proc.stdout or '')})"


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
    RemediationHook("playwright_browsers", detect_playwright, report_playwright, apply_playwright),
    RemediationHook("codegen", detect_codegen, report_codegen, apply_codegen),
    RemediationHook(
        "package_manager_pin",
        detect_pm_pin,
        report_pm_pin,
        apply_pm_pin,
        files=_pm_pin_files,
    ),
    RemediationHook(
        "bun_workflow_version",
        detect_bun_workflow,
        report_bun_workflow,
        apply_bun_workflow,
        files=_bun_workflow_files,
    ),
    # Last, so it also formats a manifest package_manager_pin just rewrote.
    RemediationHook(
        "manifest_format",
        detect_manifest_format,
        report_manifest_format,
        apply_manifest_format,
    ),
]

# Register all JS hooks in the global cross-ecosystem registry on import.
for _hook in REMEDIATIONS:
    register(_hook)


run_remediations = make_run_remediations(lambda: REMEDIATIONS)
