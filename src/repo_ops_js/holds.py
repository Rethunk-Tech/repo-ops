"""Version-ceiling ("hold") enforcement shared across the JS package managers.

Wraps ``repo_ops_core.holds`` with the JS-specific pieces: reading a
package's resolved version from ``node_modules``, building an npm-registry
range spec for a ceiling, and a post-apply clamp-down (:func:`enforce_holds`)
that every ``apply_*`` calls once at the end. npm/yarn/pnpm also consult
:func:`resolve_package_hold` directly in their per-package targeted-bump
path to avoid attempting a doomed-to-be-clamped bump in the first place;
:func:`enforce_holds` is the safety net that's load-bearing for pnpm's
blanket ``update -r --latest`` (which has no per-package filter to skip
held packages up front).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from repo_ops_core import holds as core_holds
from repo_ops_core.holds import HoldRule
from repo_ops_core.version_utils import version_satisfies_ceiling
from repo_ops_js.expo import expo_hold
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules
from repo_ops_models import Change, Workspace


class _RunResult(Protocol):
    returncode: int
    stderr: str


def resolve_package_hold(ws: Workspace, package: str) -> HoldRule | None:
    """The Expo SDK ceiling when Expo governs *package*, else the ``[[hold]]`` rules.

    Expo's bundled spec wins over a configured hold: the app's native build accepts
    only that spec, and ``expo.install.exclude`` is the documented opt-out.
    """
    return expo_hold(ws, package) or core_holds.resolve_package_hold(ws, package)


def hold_version_spec(rule: HoldRule) -> str:
    """npm-registry spec for a rule: its own install spec, else the ceiling (``pkg@"<=6"``)."""
    return rule.spec or f"<={rule.max_version}"


def direct_dependency_names(pkg_data: dict[str, object]) -> set[str]:
    """All direct dependency names from a parsed ``package.json``.

    Used to build :func:`enforce_holds`' candidate set beyond just this
    run's ``changes``/``behind`` — a package already sitting above a
    *newly added* hold ceiling (installed before the operator configured
    the hold) won't appear in either list (``npm``/``pnpm``/``yarn``/``bun``
    all report it as already satisfying its declared range), so it would
    never be reconsidered without also checking the manifest's full
    dependency set.
    """
    names: set[str] = set()
    for section in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        deps = pkg_data.get(section)
        if isinstance(deps, dict):
            names.update(str(k) for k in deps)
    return names


def _resolved(ws: Workspace, name: str) -> str | None:
    """Installed version from the workspace's ``node_modules``, else the repo root's.

    A monorepo member's deps are usually hoisted to the root (npm, bun's hoisted
    linker), so the member directory alone often has no copy.
    """
    return resolved_version_from_node_modules(ws.path, name) or (
        resolved_version_from_node_modules(ws.git_repo, name) if ws.git_repo != ws.path else None
    )


def enforce_holds(
    ws: Workspace,
    candidate_names: Iterable[str],
    *,
    manager: str,
    run_install: Callable[[str, str], _RunResult],
) -> list[Change]:
    """Post-apply clamp-down: correct any candidate that landed above its ceiling.

    *candidate_names* should cover this apply's ``changes`` + ``behind``/
    ``behind_latest`` names (what a bulk latest/targeted-bump pass could
    plausibly have moved) **and** every direct dependency name from
    ``package.json`` (see :func:`direct_dependency_names`) — a package
    already sitting above a hold ceiling added *after* it was installed
    won't appear in either outdated list (its manifest range already
    matches what's installed), so it would never be reconsidered without
    also checking the full dependency set. For each candidate that has an
    active hold rule and is resolved above the
    ceiling, run *run_install* (a manager-specific ``install pkg@<=ceiling``
    closure) and re-verify. Raises if a corrective install fails or still
    doesn't satisfy the ceiling afterwards — a hold that silently didn't
    hold is worse than a loud failure.

    Returns one ``Change(name, corrected_version, over_ceiling_version)``
    per package that actually needed correcting, for the caller to fold
    into its ``behind_latest`` report bucket. Packages with a hold rule
    that were already within ceiling are left alone and not reported —
    nothing was actually held back from them.
    """
    corrected: list[Change] = []
    for name in dict.fromkeys(candidate_names):
        if not name:
            continue
        rule = resolve_package_hold(ws, name)
        if rule is None:
            continue
        resolved = _resolved(ws, name)
        if resolved is None or version_satisfies_ceiling(resolved, rule.max_version):
            continue
        over_ceiling = resolved
        proc = run_install(name, hold_version_spec(rule))
        if proc.returncode != 0:
            raise RuntimeError(
                f"hold: failed to clamp {name} back to <={rule.max_version} "
                f"({manager}): {(proc.stderr or '').strip()}"
            )
        landed = _resolved(ws, name)
        if landed is None or not version_satisfies_ceiling(landed, rule.max_version):
            raise RuntimeError(
                f"hold: {name} still at {landed or over_ceiling} after clamp "
                f"attempt, expected <= {rule.max_version} ({manager})"
            )
        corrected.append(Change(name, landed, over_ceiling))
    return corrected


def fold_held_into_behind(held: list[Change], behind: list[Change]) -> list[Change]:
    """Merge :func:`enforce_holds`' corrections into ``behind`` by name.

    A package ``enforce_holds`` had to correct didn't move all the way — the
    hold pinned it below what the bulk/targeted bump landed — so it belongs
    in the caller's behind_latest report, replacing any prior entry for the
    same name. Callers run their own ``enforce_holds(...)`` first (kept local
    so a test can patch that call per manager module) and pass the result in
    here for the merge.
    """
    if not held:
        return behind
    return [c for c in behind if c.name not in {h.name for h in held}] + held
