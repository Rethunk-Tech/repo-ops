"""bun upgrade check/apply."""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.process import run
from repo_ops_core.version_utils import version_satisfies_ceiling
from repo_ops_js.expo import with_expo_alignment
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
    resolve_package_hold,
)
from repo_ops_js.overrides import release_unheld_pin
from repo_ops_js.targeted_bump import run_targeted_bump
from repo_ops_js.workspaces import (
    apply_with_members,
    check_with_members,
)
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
    packages whose ``latest`` an active hold rule blocks.

    A package with an active hold rule whose ceiling ``target`` would
    violate is not promoted into ``changes`` — it lands in ``behind_latest``
    instead. ``ws`` is optional and keyword-only so direct unit tests
    exercising the parser in isolation (no ``Workspace``) keep prior
    behavior; ``check_bun`` always passes the real workspace.
    """
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
        if cur != latest and latest not in ("?", ""):
            held_rule = resolve_package_hold(ws, str(name)) if ws is not None else None
            if held_rule is not None and not version_satisfies_ceiling(
                latest, held_rule.max_version
            ):
                behind_latest.append(Change(str(name), cur, latest))
            else:
                changes.append(Change(str(name), cur, latest))
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
    packages whose ``latest`` an active hold rule's ceiling would violate. ``ws`` is optional and keyword-only so direct
    unit tests exercising the parser in isolation keep prior behavior;
    ``check_bun`` always passes the real workspace.
    """
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
        target = cells[4]
        if not current or not target or current == target:
            continue
        held_rule = resolve_package_hold(ws, name) if ws is not None else None
        if held_rule is not None and not version_satisfies_ceiling(target, held_rule.max_version):
            behind_latest.append(Change(name, current, target))
            continue
        changes.append(Change(name, current, target))
    return changes, behind_latest


def check_bun(ws: Workspace, *, members: bool = True) -> tuple[list[Change], list[Change]]:
    """Check for outdated bun packages.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` holds packages
    whose ``latest`` an active hold blocks.

    ``bun outdated`` reports only the workspace its cwd belongs to, so a monorepo
    root also checks each member directory; ``members=False`` is the
    one-directory form ``apply_bun`` runs per directory.
    """
    return check_with_members(ws, _check_bun_dir) if members else _check_bun_dir(ws)


def _check_bun_dir(ws: Workspace) -> tuple[list[Change], list[Change]]:
    proc_json = run(["bun", "outdated", "--json"], ws.path, timeout=TIMEOUT_CHECK)
    if proc_json.returncode in (0, 1) and proc_json.stdout.strip():
        try:
            data = json.loads(proc_json.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            changes, behind = _changes_from_bun_json(proc_json.stdout, ws=ws)
            return with_expo_alignment(ws, changes), behind

    proc = run(["bun", "outdated"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed")
    changes, behind = _changes_from_bun_text(proc.stdout, ws=ws)
    return with_expo_alignment(ws, changes), behind


def _exact_flag(name: str, pkg_data: dict[str, object]) -> list[str]:
    """``--exact`` for a dep pinned to one version; ``bun add`` otherwise saves ``^``."""
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        deps = pkg_data.get(section)
        if isinstance(deps, dict) and name in deps:
            return ["--exact"] if str(deps[name]).strip()[:1].isdigit() else []
    return []


def targeted_bump_behind(
    ws: Workspace, behind: list[Change], *, root: Path | None = None
) -> tuple[list[Change], list[Change]]:
    """Per-package ``bun add <pkg>@<version>`` fallback for still-behind deps.

    Mirrors npm's ``targeted_bump_behind``: for each behind-latest direct dep we
    snapshot the workspace's lockfile/manifests, run a targeted
    ``bun add <pkg>@<version>``, and keep the bump only if install succeeds
    **and** the resolved version in node_modules actually advanced (guards the
    ghost-bump case where the lock churns but the resolver holds the old
    version due to an override/exact-pin). On any failure or no-op we restore
    the snapshot, leaving that package behind.

    Reversible per-package: a bump
    that fights peer constraints is dropped, never committed. Returns
    ``(applied, still_behind)`` — ``applied`` carries the real resolved version
    landed.
    """
    if not behind:
        return [], behind
    # The lockfile lives at the monorepo root: snapshot there, and read its
    # node_modules when the member directory has no linked copy.
    mdir = root or ws.path
    pkg_data = read_json_file(ws.path / "package.json") or {}

    def run_bump(name: str, version: str) -> bool:
        # A ``~``/``^`` target is a hold's install spec (Expo's), saved as written.
        exact = [] if version[:1] in ("~", "^") else _exact_flag(name, pkg_data)
        install = run(
            ["bun", "add", *exact, f"{name}@{version}"],
            ws.path,
            timeout=TIMEOUT_APPLY,
        )
        return install.returncode == 0

    def resolve_landed(name: str) -> str | None:
        return resolved_version_from_node_modules(
            ws.path, name
        ) or resolved_version_from_node_modules(mdir, name)

    applied: list[Change] = []
    still_behind: list[Change] = []
    for change in behind:
        resolved = run_targeted_bump(
            ws,
            mdir,
            change,
            manager="bun",
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
    ws: Workspace, changes: list[Change], behind: list[Change], *, mutated: bool, root: Path
) -> list[Change]:
    """Shared tail for every ``apply_bun`` exit path (bare/targeted).

    Runs the still-behind targeted bump, then the end-of-apply hold safety
    net (``enforce_holds``): cheap insurance even though bun's own targeting
    is already per-package (see the per-package target loop above), and the
    only mechanism that catches a package already sitting above a *newly
    added* hold ceiling from before the hold existed (``direct_dependency_names``
    covers those since they won't appear in ``changes``/``behind`` either).
    """
    applied, behind = targeted_bump_behind(ws, behind, root=root)
    candidate_names = direct_dependency_names(read_json_file(ws.path / "package.json") or {}) | {
        c.name for c in (*changes, *applied, *behind)
    }
    held = enforce_holds(
        ws,
        candidate_names,
        manager="bun",
        run_install=lambda name, spec: run(
            ["bun", "add", f"{name}@{spec}"], ws.path, timeout=TIMEOUT_APPLY
        ),
    )
    # bun records the *requested* spec in bun.lock's workspace mirror rather
    # than the range it wrote to package.json, so a bare ``bun update --latest``
    # leaves the mirror holding "latest" against a caret manifest. A plain
    # install rewrites the mirror from package.json. Only worth doing when
    # something actually mutated, and best-effort: a failure here leaves the
    # cosmetic drift an apply would have left anyway.
    if mutated or applied or held:
        run(["bun", "install"], ws.path, timeout=TIMEOUT_APPLY)
    return fold_held_into_behind(held, behind)


def apply_bun(ws: Workspace) -> list[Change]:
    """Apply at the root and in every workspace member (see :func:`_apply_bun_dir`)."""
    return apply_with_members(ws, _apply_bun_dir)


def _apply_bun_dir(ws: Workspace, root: Path) -> list[Change]:
    """Apply upgrades ``check_bun`` reports for one directory, without rewriting ranges to ``latest``.

    Each dep that ``check_bun`` left behind_latest gets a reversible targeted
    ``bun add <pkg>@latest``; only bumps that actually advance the resolved
    version are kept. Returns the subset of behind_latest that could not be
    moved.
    """
    changes, behind = check_bun(ws, members=False)
    targets: list[str] = []
    requested: list[Change] = []
    for ch in changes:
        name = (ch.name or "").strip()
        if not name or not _valid_bun_package_name(name):
            continue
        # An active hold ceiling wins over the check_bun-derived target.
        held_rule = resolve_package_hold(ws, name)
        hold_spec = hold_version_spec(held_rule) if held_rule is not None else None
        version = hold_spec or (ch.to or "").strip()
        if not version or version in ("?", "latest", "|"):
            continue
        targets.append(f"{name}@{version}")
        requested.append(Change(name, ch.from_ver, version))
    if not changes and not targets:
        return _finalize_bun_apply(ws, changes, behind, root=root, mutated=False)
    if not targets:
        # Bare ``bun update`` — no per-package target args; the guard is still
        # applied to any changes whose target version IS known (skip
        # unparseable '?' entries but guard what IS known from the original
        # check output).
        proc = run(["bun", "update"], ws.path, timeout=TIMEOUT_APPLY)
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
            # Defer to the targeted-bump retry (which can release an unheld
            # legacy pin) instead of hard-failing here; a still-blocked
            # package after that best-effort retry lands in behind_latest.
            blocked = unresolved_targets(ws, guardable)
            behind = fold_blocked_into_behind(blocked, behind)
        return _finalize_bun_apply(ws, changes, behind, root=root, mutated=True)
    proc = run(["bun", "update", "--", *targets], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")
    blocked = unresolved_targets(ws, requested)
    behind = fold_blocked_into_behind(blocked, behind)
    return _finalize_bun_apply(ws, changes, behind, root=root, mutated=True)
