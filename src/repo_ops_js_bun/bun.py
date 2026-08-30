"""bun upgrade check/apply."""

from __future__ import annotations

import json
import re

from repo_ops_core.config import (
    ambient_env,
    coerce_bool_setting,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run
from repo_ops_js.audit_overrides import make_audit_override_api
from repo_ops_js.guards import (
    assert_targets_resolved,
    fold_blocked_into_behind,
    unresolved_targets,
)
from repo_ops_js.holds import (
    direct_dependency_names,
    enforce_holds,
    fold_held_into_behind,
    hold_version_spec,
    is_within_ceiling,
    resolve_package_hold,
)
from repo_ops_js.overrides import release_unheld_pin
from repo_ops_js.targeted_bump import run_targeted_bump
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules
from repo_ops_models import Change, Workspace

# `bun outdated` has no --json flag (as of bun 1.x; the codebase checks
# opportunistically via check_bun but falls back here when JSON is absent).
# It always prints a table led by a `bun outdated vX (hash)` banner.  Parse
# that table row-by-row rather than whitespace-splitting (which mis-read the
# banner as a package named "bun" at version "(hash)" and fed
# `bun update bun@(hash)` → hard failure).
#
# The column separator depends on bun's terminal capability detection: Unicode
# box drawing (`│`) on a UTF-8-capable terminal, plain ASCII pipes (`|`)
# otherwise — CI runners and minimal containers get the ASCII table.
# Both dialects share the same cell structure, so rows are split on whichever
# separator leads the line.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_BOX_V = "│"  # │ box drawings light vertical (column separator)
_ASCII_V = "|"  # ASCII fallback separator on non-UTF-8 terminals
_DEP_GROUP_SUFFIX_RE = re.compile(r"\s*\((?:dev|peer|optional)\)\s*$")
_PKG_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$")
_BUN_UPDATE_LATEST_ENV = "REPO_OPS_BUN_UPDATE_LATEST"
_BUN_TARGETED_BUMP_ENV = "REPO_OPS_BUN_TARGETED_BUMP"

# Audit version overrides: package → advisory-safe version to use instead of
# the check_bun-derived (range-respecting) target. Populated by
# repo_ops_audit.audit.apply_remediations before the orchestration apply call
# and cleared in a finally block after (mirrors repo_ops_js_npm.npm's
# _AUDIT_VERSION_OVERRIDES). Not thread-safe, but apply_remediations processes
# workspaces sequentially. Empty outside of audit --fix runs.
_AUDIT_VERSION_OVERRIDES: dict[str, str] = {}

set_bun_audit_version_overrides, clear_bun_audit_version_overrides = make_audit_override_api(
    _AUDIT_VERSION_OVERRIDES
)


def _bun_update_latest_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "bun_update_latest",
        cli_value=process_env_cli_override(_BUN_UPDATE_LATEST_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    return coerce_bool_setting(raw)


def _bun_targeted_bump_enabled(ws: Workspace | None = None) -> bool:
    raw = resolve_setting(
        "bun_targeted_bump",
        cli_value=process_env_cli_override(_BUN_TARGETED_BUMP_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    return coerce_bool_setting(raw)


def _valid_bun_package_name(name: str) -> bool:
    n = (name or "").strip()
    # Reject only the table header (``Package``) and dash-led junk. A real npm
    # dependency literally named ``bun`` is valid: the ``bun outdated`` banner is
    # filtered by STRUCTURE (it is not a ``│``-delimited row) in
    # ``_changes_from_bun_text``, not by excluding the name ``bun`` here.
    if not n or n.startswith("-") or n == "Package":
        return False
    return bool(_PKG_NAME_RE.match(n))


def _changes_from_bun_json(
    stdout: str, *, ws: Workspace | None = None
) -> tuple[list[Change], list[Change]]:
    """Forward-compatible parser for a future `bun outdated --json`.

    Range-respecting by default (``update`` field); ``latest`` when opted in.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` contains
    packages that have a newer ``latest`` version available but are not
    actionable because latest-mode is off (and the in-range update equals
    the current version).

    A package with an active hold rule whose ceiling ``target`` would
    violate is not promoted into ``changes`` — it lands in ``behind_latest``
    instead. ``ws`` is optional and keyword-only so direct unit tests
    exercising the parser in isolation (no ``Workspace``) keep prior
    behavior; ``check_bun`` always passes the real workspace.
    """
    want_latest = _bun_update_latest_enabled(ws)
    changes: list[Change] = []
    behind_latest: list[Change] = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return changes, behind_latest
    if not isinstance(data, dict):
        return changes, behind_latest
    for name, meta in data.items():
        if not _valid_bun_package_name(str(name)) or not isinstance(meta, dict):
            continue
        cur = str(meta.get("current") or meta.get("installed") or "?")
        latest = str(meta.get("latest", "?"))
        target = latest if want_latest else str(meta.get("update") or meta.get("latest") or "?")
        if cur != target and target not in ("?", ""):
            held_rule = resolve_package_hold(ws, str(name)) if ws is not None else None
            if held_rule is not None and not is_within_ceiling(target, held_rule):
                behind_latest.append(Change(str(name), cur, target))
            else:
                changes.append(Change(str(name), cur, target))
        elif not want_latest and latest not in ("?", "") and cur != latest:
            # In-range is already current, but a newer latest exists.
            behind_latest.append(Change(str(name), cur, latest))
    return changes, behind_latest


def _changes_from_bun_text(
    stdout: str, *, ws: Workspace | None = None
) -> tuple[list[Change], list[Change]]:
    """Parse ``bun outdated``'s table (Unicode box drawing or ASCII pipes).

    Columns: ``Package │ Current │ Update │ Latest``. ``Update`` is the newest
    version within the package.json range; ``Latest`` ignores the range. Only
    separator-delimited data rows are read, so the banner, the ``Resolving…``
    line, the box borders, and the ``Package`` header are all skipped.  On
    non-UTF-8 terminals (CI runners, minimal containers) bun renders the same
    table with ASCII ``|`` separators; ASCII border rows
    (``|-----|-----|``) split into dash-led cells that
    ``_valid_bun_package_name`` rejects.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` contains
    packages that have a newer ``latest`` version available but are not
    actionable because latest-mode is off (and the in-range update equals
    the current version) — or because an active hold rule's ceiling would be
    violated by ``target``. ``ws`` is optional and keyword-only so direct
    unit tests exercising the parser in isolation keep prior behavior;
    ``check_bun`` always passes the real workspace.
    """
    want_latest = _bun_update_latest_enabled(ws)
    changes: list[Change] = []
    behind_latest: list[Change] = []
    for raw in stdout.splitlines():
        line = _ANSI_RE.sub("", raw).strip()
        if line.startswith(_BOX_V):
            sep = _BOX_V
        elif line.startswith(_ASCII_V):
            sep = _ASCII_V
        else:
            continue
        # "│ name │ current │ update │ latest │" -> ['', name, cur, upd, lat, '']
        cells = [c.strip() for c in line.split(sep)]
        if len(cells) < 6:
            continue
        name = _DEP_GROUP_SUFFIX_RE.sub("", cells[1]).strip()
        if name == "Package" or not _valid_bun_package_name(name):
            continue
        current = cells[2]
        update_ver = cells[3]
        latest_ver = cells[4]
        target = latest_ver if want_latest else update_ver
        if not current or not target or current == target:
            # When latest-mode is off, check if there's a newer latest to report.
            if not want_latest and latest_ver and current and current != latest_ver:
                behind_latest.append(Change(name, current, latest_ver))
            continue
        held_rule = resolve_package_hold(ws, name) if ws is not None else None
        if held_rule is not None and not is_within_ceiling(target, held_rule):
            behind_latest.append(Change(name, current, target))
            continue
        changes.append(Change(name, current, target))
    return changes, behind_latest


def check_bun(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated bun packages.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` holds packages
    that have a newer ``latest`` version available but are not actioned when
    ``REPO_OPS_BUN_UPDATE_LATEST`` is unset.
    """
    proc_json = run(["bun", "outdated", "--json"], ws.path, timeout=resolve_timeout_check())
    if proc_json.returncode in (0, 1) and proc_json.stdout.strip():
        try:
            data = json.loads(proc_json.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return _changes_from_bun_json(proc_json.stdout, ws=ws)

    proc = run(["bun", "outdated"], ws.path, timeout=resolve_timeout_check())
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed")
    return _changes_from_bun_text(proc.stdout, ws=ws)


def targeted_bump_behind(ws: Workspace, behind: list[Change]) -> tuple[list[Change], list[Change]]:
    """Per-package ``bun add <pkg>@<version>`` fallback for still-behind deps.

    Mirrors npm's ``targeted_bump_behind``: for each behind-latest direct dep we
    snapshot the workspace's lockfile/manifests, run a targeted
    ``bun add <pkg>@<version>``, and keep the bump only if install succeeds
    **and** the resolved version in node_modules actually advanced (guards the
    ghost-bump case where the lock churns but the resolver holds the old
    version due to an override/exact-pin). On any failure or no-op we restore
    the snapshot, leaving that package behind.

    Opt-in (``REPO_OPS_BUN_TARGETED_BUMP``) and reversible per-package: a bump
    that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version
    landed.
    """
    if not behind or not _bun_targeted_bump_enabled(ws):
        return [], behind

    def run_bump(name: str, version: str) -> bool:
        install = run(
            ["bun", "add", f"{name}@{version}"],
            ws.path,
            timeout=resolve_timeout_apply(),
        )
        return install.returncode == 0

    def resolve_landed(name: str) -> str | None:
        return resolved_version_from_node_modules(ws.path, name)

    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        resolved = run_targeted_bump(
            ws,
            ws.path,
            change,
            manager="bun",
            audit_overrides=_AUDIT_VERSION_OVERRIDES,
            run_bump=run_bump,
            resolve_landed=resolve_landed,
            release_pin=release_unheld_pin,
            verify=assert_targets_resolved,
        )
        if resolved is None:
            still_behind.append(change)
        else:
            applied.append(resolved)
    return applied, still_behind


def _finalize_bun_apply(
    ws: Workspace, changes: list[Change], behind: list[Change], *, mutated: bool
) -> list[Change]:
    """Shared tail for every ``apply_bun`` exit path (bare/targeted/audit-only).

    Runs the still-behind targeted bump, then the end-of-apply hold safety
    net (``enforce_holds``): cheap insurance even though bun's own targeting
    is already per-package (see the per-package target loop above), and the
    only mechanism that catches a package already sitting above a *newly
    added* hold ceiling from before the hold existed (``direct_dependency_names``
    covers those since they won't appear in ``changes``/``behind`` either).
    """
    applied, behind = targeted_bump_behind(ws, behind)
    candidate_names = direct_dependency_names(read_json_file(ws.path / "package.json") or {}) | {
        c.name for c in (*changes, *applied, *behind)
    }
    held = enforce_holds(
        ws,
        candidate_names,
        manager="bun",
        run_install=lambda name, spec: run(
            ["bun", "add", f"{name}@{spec}"], ws.path, timeout=resolve_timeout_apply()
        ),
    )
    # bun records the *requested* spec in bun.lock's workspace mirror rather
    # than the range it wrote to package.json, so a bare ``bun update --latest``
    # leaves the mirror holding "latest" against a caret manifest. A plain
    # install rewrites the mirror from package.json. Only worth doing when
    # something actually mutated, and best-effort: a failure here leaves the
    # cosmetic drift an apply would have left anyway.
    if mutated or applied or held:
        run(["bun", "install"], ws.path, timeout=resolve_timeout_apply())
    return fold_held_into_behind(held, behind)


def apply_bun(ws: Workspace) -> list[Change]:
    """Apply upgrades reported by ``check_bun`` without rewriting ranges to ``latest``.

    Advisory-safe version overrides (``set_bun_audit_version_overrides``, wired
    by ``repo_ops_audit.audit.apply_remediations`` during ``audit --fix``) are
    applied independent of ``check_bun``'s findings: an overridden package is
    targeted at its safe version even when ``bun outdated`` reports it as
    already in-range (empty ``changes``) or only behind on ``latest``; dropping
    it in that case would silently skip an advisory fix.

    With ``REPO_OPS_BUN_TARGETED_BUMP`` set, each dep that ``check_bun`` left
    behind_latest gets a reversible targeted ``bun add <pkg>@latest``; only
    bumps that actually advance the resolved version are kept. Returns the
    subset of behind_latest that could not be moved (empty when the flag is
    off or all behind deps were successfully targeted).
    """
    changes, behind = check_bun(ws)
    targets: list[str] = []
    requested: list[Change] = []
    seen_names: set[str] = set()
    for ch in changes:
        name = (ch.name or "").strip()
        if not name or not _valid_bun_package_name(name):
            continue
        # Precedence: advisory-safe override (audit --fix) first, then an
        # active hold ceiling, then the raw check_bun-derived target.
        held_rule = resolve_package_hold(ws, name)
        hold_spec = hold_version_spec(held_rule) if held_rule is not None else None
        version = _AUDIT_VERSION_OVERRIDES.get(name) or hold_spec or (ch.to or "").strip()
        if not version or version in ("?", "latest", "|"):
            continue
        targets.append(f"{name}@{version}")
        requested.append(Change(name, ch.from_ver, version))
        seen_names.add(name)
    # Advisory-safe overrides for packages check_bun did NOT report as changed
    # (already satisfies its declared range, or only behind_latest) must still
    # be targeted explicitly here — silently dropping them skips the fix.
    for name, safe_version in _AUDIT_VERSION_OVERRIDES.items():
        name = (name or "").strip()
        safe_version = (safe_version or "").strip()
        if not name or name in seen_names or not safe_version or not _valid_bun_package_name(name):
            continue
        targets.append(f"{name}@{safe_version}")
        requested.append(Change(name, "", safe_version))
        seen_names.add(name)
    if not changes and not targets:
        return _finalize_bun_apply(ws, changes, behind, mutated=False)
    if not targets:
        # Bare ``bun update`` — no per-package target args; the guard is still
        # applied to any changes whose target version IS known (skip
        # unparseable '?' entries but guard what IS known from the original
        # check output).
        proc = run(["bun", "update"], ws.path, timeout=resolve_timeout_apply())
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "bun update failed")
        # Re-check original changes for known target versions.
        guardable = [
            ch
            for ch in changes
            if (ch.to or "").strip() not in ("", "?", "latest", "|")
            and _valid_bun_package_name((ch.name or "").strip())
        ]
        if guardable:
            if _bun_targeted_bump_enabled(ws):
                # Defer to the targeted-bump retry below (which can release
                # an unheld legacy pin) instead of hard-failing here; a
                # still-blocked package after that best-effort retry lands
                # in behind_latest like any other targeted-bump miss.
                blocked = unresolved_targets(ws, guardable)
                behind = fold_blocked_into_behind(blocked, behind)
            else:
                assert_targets_resolved(ws, guardable, manager="bun")
        return _finalize_bun_apply(ws, changes, behind, mutated=True)
    proc = run(["bun", "update", "--", *targets], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")
    if _bun_targeted_bump_enabled(ws):
        blocked = unresolved_targets(ws, requested)
        behind = fold_blocked_into_behind(blocked, behind)
    else:
        assert_targets_resolved(ws, requested, manager="bun")
    return _finalize_bun_apply(ws, changes, behind, mutated=True)
