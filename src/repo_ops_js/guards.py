"""Post-apply ghost-bump resolution guard (shared across JS package managers).

Bug #112: a ``bun update`` (or ``npm update`` / ``pnpm update``) can churn the
lockfile while an ``overrides``/``resolutions``/``pnpm.overrides`` entry or an
exact-pin dependency range keeps the OLD version installed.  ``verify``
(build/test) then passes against the un-upgraded tree and the bump is reported
green when nothing actually moved.

This module exposes :func:`assert_targets_resolved` which every JS apply path
calls after running the package-manager update command.  It reads the resolved
version from ``node_modules`` and raises a clear ``RuntimeError`` when any
requested target is still behind, so ``apply_workspace`` marks the workspace
``fail`` instead of silently lying.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules
from repo_ops_models import Change, Workspace

# ---------------------------------------------------------------------------
# Semver pre-release-aware comparison (stdlib only, task 4)
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)(?:-([\w.+-]+))?(?:\+([\w.+-]+))?$")


def _parse_semver(v: str) -> tuple[int, int, int, str]:
    """Parse a semver string into (major, minor, patch, pre_release).

    Strips leading operators (``^``, ``~``, ``>=``, …) before parsing.
    Returns ``(0, 0, 0, '')`` for anything unparseable.
    """
    cleaned = re.sub(r"^[\s^~>=<!]+", "", (v or "").strip())
    m = _SEMVER_RE.match(cleaned)
    if not m:
        return (0, 0, 0, "")
    major, minor, patch, pre = (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        m.group(4) or "",
    )
    return (major, minor, patch, pre)


def _semver_lt_pre(installed: str, target: str) -> bool:
    """Return True when *installed* is strictly less than *target*.

    Pre-release awareness (semver §11):
    - A pre-release of *exactly* the target version (same M.m.p, non-empty
      pre) is considered **less than** the stable target.  e.g.
      ``4.0.0-rc.1 < 4.0.0``.
    - A pre-release of a *higher* version is fine (it satisfies a stable
      lower target).

    This check is only used to detect ghost bumps: the installed version must
    be at or above the target.  We raise only when installed < target.
    """
    i_maj, i_min, i_pat, i_pre = _parse_semver(installed)
    t_maj, t_min, t_pat, t_pre = _parse_semver(target)

    i_nums = (i_maj, i_min, i_pat)
    t_nums = (t_maj, t_min, t_pat)

    if i_nums < t_nums:
        return True
    if i_nums > t_nums:
        return False
    # Same M.m.p — compare pre-release.  Target is expected to be stable
    # (no pre-release); installed pre-release of the same numbers is less.
    # semver §11.3: "When major, minor, and patch are equal, a pre-release
    # version has lower precedence than a normal version."
    # semver §11.3: pre-release of same M.m.p < stable
    return bool(i_pre and not t_pre)


# ---------------------------------------------------------------------------
# Public guard
# ---------------------------------------------------------------------------


def unresolved_targets(
    ws: Workspace,
    requested: list[Change],
    *,
    fallback_resolver: Callable[[Workspace, str], str | None] | None = None,
) -> list[tuple[Change, str]]:
    """Return the subset of ``requested`` still behind their target, unraised.

    Pure check (no raise) shared by :func:`assert_targets_resolved` and by
    callers that have a fallback for a blocked target — e.g. an apply path
    with ``--*-targeted-bump`` enabled defers to the per-package retry loop
    (which can also release a legacy, non-held ``resolutions``/``overrides``
    pin — see ``repo_ops_js.overrides.release_unheld_pin``) instead of
    hard-failing the whole workspace on the first pass. Each returned tuple
    is ``(change, resolved_version)`` so callers can build a message or a
    ``Change`` for a retry list without re-reading ``node_modules``.
    """
    blocked: list[tuple[Change, str]] = []
    for ch in requested:
        target_raw = (ch.to or "").strip()
        if not target_raw or target_raw in ("?", "latest"):
            continue
        resolved = resolved_version_from_node_modules(ws.path, ch.name)
        if resolved is None and ws.git_repo and ws.git_repo != ws.path:
            resolved = resolved_version_from_node_modules(ws.git_repo, ch.name)
        if resolved is None and fallback_resolver is not None:
            resolved = fallback_resolver(ws, ch.name)
        if resolved is None:
            continue
        # ``<`` not ``!=``: a legitimate higher in-range resolution is a
        # success, not a ghost bump.  Only a version left behind the
        # requested target is a ghost.  Pre-release of the same version
        # numbers counts as behind (semver §11.3).
        if _semver_lt_pre(resolved, target_raw):
            blocked.append((ch, resolved))
    return blocked


def assert_targets_resolved(
    ws: Workspace,
    requested: list[Change],
    manager: str = "js",
    *,
    fallback_resolver: Callable[[Workspace, str], str | None] | None = None,
) -> None:
    """Guard against #112 ghost bumps.

    An ``overrides``/``resolutions`` entry, an exact-pin dependency range, or
    a peer/transitive constraint can shadow a package-manager update command:
    the lockfile churns but the OLD version stays installed.  ``verify``
    (build/test) then passes against the un-upgraded tree and the bump is
    reported green when nothing moved.

    Compare each requested target against the version actually resolved on
    disk; if any is still behind, raise so ``apply_workspace`` marks the
    workspace ``fail`` (which both vetoes the repo commit by default and stops
    the summary from lying) instead of reporting a successful upgrade that
    never happened.

    Resolution is read from ``node_modules`` (the workspace dir, then the
    repo root for a hoisted/workspace layout).  Deno callers may provide a
    lockfile fallback resolver for npm specifiers because Deno does not
    populate ``node_modules``.  When the version can't be read at all, we
    don't manufacture a failure — a layout we can't see is left to the
    existing ``overrides_audit`` warnings.

    Pre-release note: an installed ``X.Y.Z-rc.1`` does NOT satisfy a stable
    target ``X.Y.Z`` — it is still behind the stable release (semver §11.3).
    """
    blocked = unresolved_targets(
        ws,
        requested,
        fallback_resolver=fallback_resolver if manager == "deno" else None,
    )
    if blocked:
        detail = "; ".join(
            f"{ch.name} still at {resolved}, expected >= {(ch.to or '').strip()} (check overrides/resolutions/pins)"
            for ch, resolved in blocked
        )
        raise RuntimeError(
            f"ghost bump: {manager} upgrade blocked (override/resolution/pin/constraint), "
            "lockfile churned but dependency did not move: " + detail
        )
