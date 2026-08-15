"""Yarn Berry check/apply with isolated workspace snapshots."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from repo_ops_core.config import (
    ambient_env,
    coerce_bool_setting,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
from repo_ops_core.constants import resolve_timeout_apply
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_js.guards import assert_targets_resolved, unresolved_targets
from repo_ops_js.holds import (
    enforce_holds,
    hold_version_spec,
    is_within_ceiling,
    resolve_package_hold,
)
from repo_ops_js.overrides import (
    overrides_audit,
    package_json_paths,
    release_unheld_pin,
)
from repo_ops_js.workspaces import corepack_prepare
from repo_ops_lockfiles.parsers import versions_yarn_lock
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import YARN_ISOLATED_COPY_SKIP, YARN_ISOLATED_YARN_SKIP

# Compat alias: tests historically imported the private yarn helper.
_package_json_paths = package_json_paths

FILE_PROTOCOL_RE = re.compile(r"^file:(\.\./.+?)(?:#.*)?$")

# Audit version overrides: package → advisory-safe version to use instead of @latest.
# Populated by repo_ops_audit.audit.apply_remediations before the orchestration apply
# call and cleared in a finally block after.  Empty outside of audit --fix runs.
_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}


def set_yarn_audit_version_overrides(overrides: dict[str, str]) -> None:
    """Inject per-package advisory-safe versions for the next targeted-bump call.

    Called by ``repo_ops_audit.audit.apply_remediations`` so that
    ``targeted_bump_behind`` uses the advisory-safe version (e.g. ``yarn up pkg@1.5.5``)
    rather than always running ``yarn up pkg@latest``.
    """
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = dict(overrides)


def clear_yarn_audit_version_overrides() -> None:
    """Reset audit version overrides after each workspace apply."""
    global _AUDIT_VERSION_OVERRIDES
    _AUDIT_VERSION_OVERRIDES = {}


_STDERR_TAIL_LINES = 12
_YARN_UPDATE_LATEST_ENV = "REPO_OPS_YARN_UPDATE_LATEST"
_YARN_TARGETED_BUMP_ENV = "REPO_OPS_YARN_TARGETED_BUMP"
_TIMEOUT_NPM_INFO = 60

# Cap on concurrent yarn isolated-copy extractions. Each check/guard run copies
# the workspace to scratch and lets Yarn Berry extract its cache there, which is
# large enough that one extraction per --jobs worker fills the volume even when
# it is disk-backed. This semaphore bounds the disk-heavy extraction phase
# independently of the workspace-level --jobs pool, so non-yarn ecosystems keep
# full parallelism.
_YARN_MAX_CONCURRENCY_ENV = "REPO_OPS_YARN_MAX_CONCURRENCY"
_DEFAULT_YARN_MAX_CONCURRENCY = 4
_extraction_sem: threading.BoundedSemaphore | None = None
_extraction_sem_lock = threading.Lock()


def _extraction_root() -> str | None:
    """Directory to hold isolated yarn copies, or None for the system default.

    A full-fleet sweep extracts well over 100 GB of Yarn Berry cache across the
    run, so the destination has to be disk-backed: the usual /tmp is a tmpfs
    sized from RAM on many hosts, and filling it takes the whole machine down
    with it rather than just failing the run. An explicit TMPDIR is honored as
    the operator's stated choice; otherwise prefer /var/tmp, which is
    disk-backed by convention, and fall back to the default when it is absent.
    """
    if os.environ.get("TMPDIR", "").strip():
        return None  # tempfile already honors TMPDIR
    var_tmp = Path("/var/tmp")
    return str(var_tmp) if var_tmp.is_dir() else None


def _resolve_yarn_max_concurrency() -> int:
    """Max concurrent yarn extractions from ``REPO_OPS_YARN_MAX_CONCURRENCY`` (min 1)."""
    raw = os.environ.get(_YARN_MAX_CONCURRENCY_ENV, "").strip()
    if not raw:
        return _DEFAULT_YARN_MAX_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_YARN_MAX_CONCURRENCY
    return max(1, value)


@contextlib.contextmanager
def _yarn_extraction_slot() -> Iterator[None]:
    """Hold one extraction slot for the duration of an isolated-copy yarn run.

    Lazily sized from the env on first use (set by ``run`` before the worker
    pool starts), so the cap applies to the whole process.
    """
    global _extraction_sem
    if _extraction_sem is None:
        with _extraction_sem_lock:
            if _extraction_sem is None:
                _extraction_sem = threading.BoundedSemaphore(
                    _resolve_yarn_max_concurrency()
                )
    _extraction_sem.acquire()
    try:
        yield
    finally:
        _extraction_sem.release()


# Per-run probe cache: resolved workspace path → intended-targets list.
# Populated by check_yarn_snapshot so that a subsequent apply_yarn in the
# same process can reuse the result without re-running an isolated yarn up.
# Key: str(ws.path.resolve()) — avoids Path hashing inconsistencies.
_probe_cache: dict[str, list[Change]] = {}


def clear_probe_cache() -> None:
    """Empty the per-run probe cache.

    Call between workspace runs in tests to prevent cross-test contamination.
    Mirrors the pattern used by ``register_go_work_dir`` / ``_go_work_dirs``
    in go.py.
    """
    _probe_cache.clear()


def _workspace_declares_resolutions(ws: Workspace) -> bool:
    """Return True when any workspace package.json pins via resolutions.

    The ghost-bump probe exists only to detect pins that silently block
    ``yarn up``. When no ``resolutions`` / ``packageResolutions`` entries
    exist, a second isolated install is pure overhead — skip it.
    """
    for pkg_path in package_json_paths(ws.path):
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("resolutions", "packageResolutions"):
            res = data.get(key)
            if isinstance(res, dict) and res:
                return True
    return False


# Yarn 4 defaults to Plug'n'Play when no nodeLinker setting is present.
# PnP repos have no node_modules, so the ghost-bump guard (which reads the
# installed version from node_modules) cannot operate.  We detect PnP by:
#   1. Sentinel files written by PnP at install time (.pnp.cjs / .pnp.loader.mjs)
#   2. nodeLinker: node-modules (or node-modules-pnp) absent in .yarnrc.yml
# If either marker is present, we skip the guard and emit a stderr notice.
_PNP_SENTINELS = (".pnp.cjs", ".pnp.loader.mjs")


def _is_pnp_workspace(ws_path: Path) -> bool:
    """Return True when the yarn workspace uses Plug'n'Play (no node_modules).

    Detection is intentionally conservative: if we cannot determine the linker
    we assume node-modules is NOT in use (i.e. PnP), to avoid false-fail on
    layouts we cannot read.  The integration fixture explicitly sets
    ``nodeLinker: node-modules`` so it is guarded.
    """
    # Fast path: PnP sentinel files present after install
    for sentinel in _PNP_SENTINELS:
        if (ws_path / sentinel).exists():
            return True
    # Check .yarnrc.yml for nodeLinker setting
    yarnrc = ws_path / ".yarnrc.yml"
    if yarnrc.is_file():
        try:
            content = yarnrc.read_text(encoding="utf-8")
        except OSError:
            return True  # can't read config → assume PnP (safe skip)
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("nodeLinker:"):
                value = stripped[len("nodeLinker:") :].strip().strip('"').strip("'")
                return value not in ("node-modules", "pnpm")
        # nodeLinker not set at all → Yarn 4 default is PnP
        return True
    # No .yarnrc.yml — could be Yarn 1/Classic (always node-modules).
    # Guard is safe; don't skip.
    return False


def _yarn_update_latest_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "yarn_update_latest",
        cli_value=process_env_cli_override(_YARN_UPDATE_LATEST_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    return coerce_bool_setting(raw)


def _yarn_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "yarn_targeted_bump",
        cli_value=process_env_cli_override(_YARN_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    return coerce_bool_setting(raw)


def _yarn_up_cmd() -> list[str]:
    """In-range resolution refresh: ``yarn up -R '*'`` in both modes.

    Bare ``yarn up`` (no patterns) is a no-op in Yarn 4 — it resolves and
    installs but upgrades nothing, so conservative mode never moved a single
    in-range bump (issue #114). ``-R '*'`` re-resolves every dependency to the
    newest version satisfying the existing package.json range without touching
    the range itself. Latest mode differs downstream, not here: it adds
    behind-latest reporting (#110) and targeted range-breaking bumps (#111).
    """
    return ["yarn", "up", "-R", "*"]


def _stderr_tail(proc: subprocess.CompletedProcess[str]) -> str:
    combined = (
        (proc.stderr or "")
        + ("\n" if proc.stderr and proc.stdout else "")
        + (proc.stdout or "")
    )
    lines = [ln for ln in combined.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = "\n".join(lines[-_STDERR_TAIL_LINES:])
    return f"{tail}\n" if tail else ""


def _command_failure(
    label: str, proc: subprocess.CompletedProcess[str]
) -> RuntimeError:
    detail = _stderr_tail(proc)
    msg = proc.stderr.strip() or label
    if detail and detail.strip() not in msg:
        msg = f"{msg}\n{detail.rstrip()}"
    return RuntimeError(msg)


def _isolated_yarn_ignore(src: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in YARN_ISOLATED_COPY_SKIP}
    if Path(src).name == ".yarn":
        ignored.update(name for name in names if name in YARN_ISOLATED_YARN_SKIP)
    return ignored


def _file_protocol_targets(ws_path: Path) -> list[Path]:
    targets: list[Path] = []
    seen: set[Path] = set()
    for pkg_json in package_json_paths(ws_path):
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        base = pkg_json.parent
        for section in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            deps = data.get(section) or {}
            if not isinstance(deps, dict):
                continue
            for spec in deps.values():
                if not isinstance(spec, str):
                    continue
                m = FILE_PROTOCOL_RE.match(spec.strip())
                if not m:
                    continue
                target = (base / m.group(1)).resolve()
                if target.exists() and target not in seen:
                    seen.add(target)
                    targets.append(target)
    return targets


def _layout_root(source_root: Path, extras: list[Path]) -> Path:
    if not extras:
        return source_root.parent
    candidates = [source_root, *extras]
    common_parts: list[str] = []
    split_paths = [p.parts for p in candidates]
    for parts in zip(*split_paths, strict=False):
        if len(set(parts)) == 1:
            common_parts.append(parts[0])
        else:
            break
    if not common_parts:
        return source_root.parent
    common = Path(*common_parts)
    if common in candidates:
        return common.parent
    return common


def _copy_tree(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src,
        dest,
        symlinks=True,
        ignore=_isolated_yarn_ignore,
        ignore_dangling_symlinks=True,
    )


def _copy_yarn_workspace_to_temp(ws: Workspace, temp_root: Path) -> Path:
    try:
        rel_ws = ws.path.relative_to(ws.git_repo)
        source_root = ws.git_repo
    except ValueError:
        rel_ws = Path(".")
        source_root = ws.path
    extras = [
        p
        for p in _file_protocol_targets(ws.path)
        if p.is_dir() and p != source_root and not p.is_relative_to(source_root)
    ]
    layout_root = _layout_root(source_root, extras)
    trees = {source_root, *extras}
    for root in trees:
        rel = root.relative_to(layout_root)
        dest = temp_root / rel
        if dest.exists():
            continue
        _copy_tree(root, dest)
    dest_ws_root = (temp_root / source_root.relative_to(layout_root)).resolve()
    return (dest_ws_root / rel_ws).resolve()


def _direct_dep_names(ws_path: Path) -> set[str]:
    """Direct dependency names declared across the workspace's package.json files.

    Deliberately narrower than ``repo_ops_js.holds.direct_dependency_names``:
    aggregates across every monorepo member manifest (not just the root) and
    excludes ``peerDependencies``, which yarn's candidate set has never
    included.
    """
    names: set[str] = set()
    for pkg_json in package_json_paths(ws_path):
        data = read_json_file(pkg_json) or {}
        for section in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
        ):
            deps = data.get(section)
            if isinstance(deps, dict):
                names.update(k for k in deps if isinstance(k, str))
    return names


def _resolved_versions(lock_path: Path) -> dict[str, str]:
    """name -> highest resolved version from a yarn.lock (handles duplicate ranges)."""
    resolved: dict[str, str] = {}
    for entry in versions_yarn_lock(lock_path):
        name, _, ver = entry.rpartition("@")
        if not name or not ver:
            continue
        prev = resolved.get(name)
        try:
            if prev is None or semver_tuple(ver) > semver_tuple(prev):
                resolved[name] = ver
        except (TypeError, ValueError):
            resolved.setdefault(name, ver)
    return resolved


def _latest_published(mdir: Path, name: str) -> str | None:
    """Latest published version via ``yarn npm info`` (None on any failure)."""
    try:
        proc = run(
            ["yarn", "npm", "info", name, "--json"], mdir, timeout=_TIMEOUT_NPM_INFO
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    dist_tags = data.get("dist-tags")
    if isinstance(dist_tags, dict):
        latest = dist_tags.get("latest")
        if isinstance(latest, str):
            return latest
    ver = data.get("version")
    return ver if isinstance(ver, str) else None


def behind_after_up(mdir: Path, *, ws: Workspace | None = None) -> list[Change]:
    """Direct deps with a newer published version that ``yarn up`` did not apply.

    yarn's resolver (especially under ``yarn up -R '*'``) can keep a package below
    its newest release for peer/group constraints, and repo-ops lands exactly what
    yarn produces — silently. This surfaces those gaps (issue #110) as ``behind_latest``
    so a supersede run reads honestly instead of "everything is current".

    Only runs in latest mode (``--latest`` / REPO_OPS_YARN_UPDATE_LATEST), where the
    gap is meaningful and the extra registry lookups are warranted. Resilient: any
    ``yarn npm info`` failure skips that package rather than failing the run.
    """
    if not _yarn_update_latest_enabled(ws):
        return []
    lock = mdir / "yarn.lock"
    if not lock.is_file():
        return []
    resolved = _resolved_versions(lock)
    behind: list[Change] = []
    for name in sorted(_direct_dep_names(mdir)):
        cur = resolved.get(name)
        if not cur:
            continue
        latest = _latest_published(mdir, name)
        if not latest or latest == cur:
            continue
        try:
            if semver_tuple(latest) > semver_tuple(cur):
                behind.append(Change(name, cur, latest))
        except (TypeError, ValueError):
            continue
    return behind


def targeted_bump_behind(
    mdir: Path, behind: list[Change], *, ws: Workspace | None = None
) -> tuple[list[Change], list[Change]]:
    """Per-package ``yarn up <pkg>@latest`` fallback for still-behind deps (issue #111).

    Option 2 of #110: rather than only reporting the gap ``behind_after_up`` found,
    optionally *move* it. For each still-behind direct dep we snapshot the workspace's
    lockfile/manifests, run a targeted ``yarn up <pkg>@latest`` + ``yarn install``, and
    keep the bump only if install succeeds **and** the resolved version actually advanced
    (guards the ghost-bump case where the lock churns but the resolver holds the old
    version). On any failure or no-op we restore the snapshot, leaving that package behind.

    On by default (``REPO_OPS_YARN_TARGETED_BUMP``) and reversible
    per-package: a bump that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version landed.

    ``ws`` carries the real workspace context (for hold-rule lookups against the
    *real* repo/workspace path, not the ``mdir`` isolated copy this function may be
    operating on from ``check_yarn_snapshot``). It is optional and keyword-only so
    direct callers that predate holds (including existing tests) keep the prior
    audit-override/latest fallback behavior when omitted.
    """
    if not behind or not _yarn_targeted_bump_enabled(ws):
        return [], behind
    lock = mdir / "yarn.lock"
    if not lock.is_file():
        return [], behind
    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        snap = snapshot_workspace_files(mdir, "yarn")
        # The advisory-safe version wins when set (audit --fix path) — a
        # security remediation must not be silently blocked by a routine
        # hold policy; else a hold ceiling when an active rule matches this
        # package; else @latest.
        audit_override = _AUDIT_VERSION_OVERRIDES.get(change.name)
        held_rule = (
            None
            if audit_override or ws is None
            else resolve_package_hold(ws, change.name)
        )
        if audit_override:
            target_ver = audit_override
        elif held_rule is not None:
            target_ver = hold_version_spec(held_rule)
        else:
            target_ver = "latest"
        if held_rule is None:
            # No active hold covers this package — a resolutions/overrides
            # pin here is legacy debt (predates the hold feature, or was a
            # one-off never promoted to a tracked hold), not an intentional
            # ceiling. Release it so the retry below has a real chance;
            # the snapshot restore on no-advance/failure below reverts this
            # edit too if the retry doesn't actually pan out.
            release_unheld_pin(mdir, change.name)
        up = run(
            ["yarn", "up", f"{change.name}@{target_ver}"],
            mdir,
            timeout=resolve_timeout_apply(),
        )
        install = (
            run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())
            if up.returncode == 0
            else None
        )
        landed = _resolved_versions(lock).get(change.name) if install else None
        advanced = False
        if landed:
            try:
                advanced = semver_tuple(landed) > semver_tuple(change.from_ver)
            except (TypeError, ValueError):
                advanced = landed != change.from_ver
        if landed and advanced and install and install.returncode == 0:
            applied.append(Change(change.name, change.from_ver, landed))
        else:
            restore_workspace_snapshot(mdir, snap, "yarn")
            still_behind.append(change)
    return applied, still_behind


def check_yarn_snapshot(
    ws: Workspace,
) -> tuple[list[Change], list[dict[str, str]], list[Change]]:
    """Plan via isolated copy; conservative ``yarn up`` unless latest env is set.

    Also pre-computes the ghost-bump guard probe (``_plan_without_resolutions``)
    and stores the result in ``_probe_cache`` so that a subsequent
    ``apply_yarn`` call in the same process can reuse it without spinning up a
    second isolated copy.
    """
    changes: list[Change] = []
    snapshot_overrides: list[dict[str, str]] = []
    with (
        _yarn_extraction_slot(),
        tempfile.TemporaryDirectory(
            prefix="dep-refresh-yarn-", dir=_extraction_root()
        ) as td,
    ):
        mdir = _copy_yarn_workspace_to_temp(ws, Path(td))
        before_snap = snapshot_workspace_files(mdir, "yarn")
        corepack_prepare("yarn", mdir)
        proc = run(_yarn_up_cmd(), mdir, timeout=resolve_timeout_apply())
        if proc.returncode != 0:
            raise _command_failure("yarn up failed", proc)
        proc2 = run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())
        if proc2.returncode != 0:
            raise _command_failure("yarn install failed", proc2)
        after_snap = snapshot_workspace_files(mdir, "yarn")
        changes = diff_workspace_snapshots(before_snap, after_snap, "yarn")
        pkg_path = mdir / "package.json"
        pkg_before = before_snap.get(pkg_path, b"")
        pkg_after = after_snap.get(pkg_path, b"")
        if pkg_before != pkg_after and not any(
            c.name == "package.json" for c in changes
        ):
            changes.append(Change("package.json", "changed", "changed"))
        snapshot_overrides = overrides_audit(mdir, "yarn")
        behind = behind_after_up(mdir, ws=ws)
        # Optionally move (not just report) the gap; applied bumps become planned
        # changes so plan previews what apply will land (issue #111).
        applied, behind = targeted_bump_behind(mdir, behind, ws=ws)
        for c in applied:
            # Defense in depth: targeted_bump_behind already targets the hold
            # ceiling instead of latest when a rule is active, so this should
            # never trip in practice — but a held-and-over-ceiling landed
            # version must never be promoted into the plan preview.
            held_rule = resolve_package_hold(ws, c.name)
            if held_rule is not None and not is_within_ceiling(c.to, held_rule):
                behind.append(c)
                continue
            if not any(existing.name == c.name for existing in changes):
                changes.append(c)
    # Pre-compute the ghost-bump guard probe and cache it so apply_yarn can
    # skip its own isolated copy when check ran first in the same process.
    # No resolutions ⇒ nothing for the guard to detect; skip the second isolate.
    probe = _plan_without_resolutions(ws) if _workspace_declares_resolutions(ws) else []
    _probe_cache[str(ws.path.resolve())] = probe
    return changes, snapshot_overrides, behind


def _plan_without_resolutions(ws: Workspace) -> list[Change]:
    """Run an isolated ``yarn up`` with ``resolutions`` stripped from package.json.

    ``resolutions`` (and the legacy ``packageResolutions``) entries pin packages
    below their natural in-range version.  When we copy the workspace for the
    check phase and run ``yarn up -R '*'``, those pins are still active in the
    copy — so the check sees no movement and the ghost goes undetected.

    Stripping ``resolutions`` before the check run lets the resolver freely
    move each dep to the newest satisfying version.  We then compare resolved
    versions by package name (not by range key) to avoid the ghost from the
    diff algorithm treating the same package under two different range headers
    as independent entries.

    Only direct-dep packages with a concrete upward move are returned.
    """
    with (
        _yarn_extraction_slot(),
        tempfile.TemporaryDirectory(
            prefix="dep-refresh-yarn-guard-", dir=_extraction_root()
        ) as td,
    ):
        mdir = _copy_yarn_workspace_to_temp(ws, Path(td))
        lock = mdir / "yarn.lock"
        # Capture resolved versions before stripping resolutions.
        before_resolved: dict[str, str] = (
            _resolved_versions(lock) if lock.is_file() else {}
        )
        # Strip resolutions from every package.json in the copy so yarn's
        # resolver can freely pick the newest in-range version.
        for pkg_path in package_json_paths(mdir):
            try:
                data = json.loads(pkg_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            changed = False
            for key in ("resolutions", "packageResolutions"):
                if key in data:
                    del data[key]
                    changed = True
            if changed:
                pkg_path.write_text(json.dumps(data), encoding="utf-8")
        corepack_prepare("yarn", mdir)
        proc = run(_yarn_up_cmd(), mdir, timeout=resolve_timeout_apply())
        if proc.returncode != 0:
            # Check failure means we can't determine intent; skip guard (safe).
            return []
        proc2 = run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())
        if proc2.returncode != 0:
            return []
        after_resolved: dict[str, str] = (
            _resolved_versions(lock) if lock.is_file() else {}
        )
    # Compare by name; only report concrete upward moves on direct deps.
    direct = _direct_dep_names(ws.path)
    result: list[Change] = []
    for name in direct:
        before_ver = before_resolved.get(name)
        after_ver = after_resolved.get(name)
        if not after_ver or not before_ver or after_ver == before_ver:
            continue
        try:
            moved_up = semver_tuple(after_ver) > semver_tuple(before_ver)
        except (TypeError, ValueError):
            moved_up = after_ver != before_ver
        if moved_up:
            result.append(Change(name, before_ver, after_ver))
    return result


def apply_yarn(ws: Workspace) -> list[Change]:
    """Apply in workspace; matches check policy (see ``_yarn_up_cmd``).

    Returns the direct deps a latest-mode ``yarn up`` left behind (issue #110);
    empty in conservative mode or when nothing is behind. With ``--yarn-targeted-bump``
    (issue #111), each still-behind dep gets a reversible targeted bump first, so only
    the deps that truly could not move are returned.

    Ghost-bump guard (#121): before running the real apply, this function
    probes what packages *would* move in an isolated copy with ``resolutions``
    stripped.  After the real apply completes (with resolutions in place),
    those intended targets are verified against the installed versions in
    ``node_modules``.  Any package that was blocked by a pin raises
    ``RuntimeError("ghost bump: …")`` so the workspace is marked ``fail``
    instead of silently succeeding.

    Plug'n'Play repos are skipped: PnP installs have no ``node_modules``
    directory, so the guard cannot read installed versions.  A diagnostic
    note is written to stderr in that case.
    """
    # --- ghost-bump guard: plan what *should* move without resolutions --------
    # Check whether check_yarn_snapshot already ran the probe in this process
    # and cached the result.  If so, reuse it (avoids a second isolated copy).
    # Fall back to a fresh probe when apply is called without a prior check
    # (e.g. direct invocations in integration tests or --apply-only runs).
    cache_key = str(ws.path.resolve())
    cached = _probe_cache.get(cache_key)
    if cached is not None:
        guardable = cached
    elif _workspace_declares_resolutions(ws):
        guardable = _plan_without_resolutions(ws)
    else:
        guardable = []

    # --- real apply -----------------------------------------------------------
    corepack_prepare("yarn", ws.path)
    # Advisory-safe pins from audit --fix (#140 parity with bun): apply even
    # when check/behind reported nothing and REPO_OPS_YARN_TARGETED_BUMP is off.
    audit_handled = _apply_yarn_audit_overrides(ws)
    proc = run(_yarn_up_cmd(), ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise _command_failure("yarn up failed", proc)
    proc2 = run(["yarn", "install"], ws.path, timeout=resolve_timeout_apply())
    if proc2.returncode != 0:
        raise _command_failure("yarn install failed", proc2)

    # --- ghost-bump guard: verify installs against plan ----------------------
    targeted_bump = _yarn_targeted_bump_enabled(ws)
    if guardable:
        if _is_pnp_workspace(ws.path):
            print(
                "repo-ops: yarn ghost-bump guard skipped (Plug'n'Play workspace — "
                "no node_modules to read installed versions from)",
                file=sys.stderr,
            )
        elif not targeted_bump:
            assert_targets_resolved(ws, guardable, manager="yarn")
        # else: targeted-bump is enabled — a blocked target here (often an
        # unheld resolutions/overrides pin) gets folded into the retry list
        # below instead of hard-failing the whole workspace on the first
        # pass; targeted_bump_behind can release the pin and actually move
        # it. Anything still unresolved after that best-effort retry lands
        # in behind_latest, same as any other targeted-bump miss.

    behind = behind_after_up(ws.path, ws=ws)
    if audit_handled:
        behind = [c for c in behind if c.name not in audit_handled]
    if guardable and targeted_bump and not _is_pnp_workspace(ws.path):
        blocked = unresolved_targets(ws, guardable)
        behind_names = {c.name for c in behind}
        for ch, _resolved in blocked:
            if ch.name not in behind_names:
                behind.append(ch)
                behind_names.add(ch.name)
    applied, behind = targeted_bump_behind(ws.path, behind, ws=ws)
    # End-of-apply hold safety net (#holds): clamp anything resolved above an
    # active ceiling, including a package already sitting above a *newly
    # added* hold from before the hold existed — direct_dependency_names
    # catches those since they won't appear in changes/behind either.
    candidate_names = _direct_dep_names(ws.path) | {c.name for c in (*applied, *behind)}
    held = enforce_holds(
        ws,
        candidate_names,
        manager="yarn",
        run_install=lambda name, spec: _yarn_hold_install(ws.path, name, spec),
    )
    if held:
        behind = [c for c in behind if c.name not in {h.name for h in held}] + held
    return behind


def _yarn_hold_install(
    mdir: Path, name: str, spec: str
) -> subprocess.CompletedProcess[str]:
    """Corrective ``yarn up <pkg>@<spec>`` + ``yarn install`` for ``enforce_holds``.

    Mirrors ``_apply_yarn_audit_overrides``'s two-step install shape; only the
    ``yarn up`` result is returned when it fails (no point installing after a
    failed resolution change).
    """
    up = run(["yarn", "up", f"{name}@{spec}"], mdir, timeout=resolve_timeout_apply())
    if up.returncode != 0:
        return up
    return run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())


def _apply_yarn_audit_overrides(ws: Workspace) -> set[str]:
    """Install advisory-safe versions from ``_AUDIT_VERSION_OVERRIDES``.

    Independent of ``REPO_OPS_YARN_TARGETED_BUMP`` so ``audit --fix`` pins are
    not silently skipped when the user did not also opt into targeted bump.
    Returns the set of package names that were targeted.
    """
    handled: set[str] = set()
    if not _AUDIT_VERSION_OVERRIDES:
        return handled
    mdir = ws.path
    requested: list[Change] = []
    for name, safe_version in list(_AUDIT_VERSION_OVERRIDES.items()):
        name = (name or "").strip()
        safe_version = (safe_version or "").strip()
        if not name or not safe_version:
            continue
        up = run(
            ["yarn", "up", f"{name}@{safe_version}"],
            mdir,
            timeout=resolve_timeout_apply(),
        )
        if up.returncode != 0:
            raise _command_failure(
                f"yarn up {name}@{safe_version} failed (audit override)", up
            )
        install = run(["yarn", "install"], mdir, timeout=resolve_timeout_apply())
        if install.returncode != 0:
            raise _command_failure(
                f"yarn install after audit override {name}@{safe_version} failed",
                install,
            )
        requested.append(Change(name, "", safe_version))
        handled.add(name)
    if requested and not _is_pnp_workspace(mdir):
        assert_targets_resolved(ws, requested, manager="yarn")
    return handled
