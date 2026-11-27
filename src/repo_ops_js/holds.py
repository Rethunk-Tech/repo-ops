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

import json
import re
from collections.abc import Callable, Iterable
from typing import Protocol

from repo_ops_core import holds as core_holds
from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_core.filesystem import read_json_file
from repo_ops_core.holds import HoldRule, workspace_rel
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_precedence, version_satisfies_ceiling
from repo_ops_js.expo import expo_hold
from repo_ops_lockfiles.snapshots import resolved_version_from_node_modules
from repo_ops_models import Change, Workspace


class _RunResult(Protocol):
    returncode: int
    stderr: str


def vscode_types_hold(ws: Workspace, package: str) -> HoldRule | None:
    """``@types/vscode`` capped at the ``engines.vscode`` floor's major.minor.

    vsce refuses to package an extension whose typings are newer than the oldest
    VS Code it declares support for; raising that floor is a product decision.
    """
    if package != "@types/vscode":
        return None
    engines = (read_json_file(ws.path / "package.json") or {}).get("engines")
    engine = engines.get("vscode") if isinstance(engines, dict) else None
    match = re.match(r"^\D*(\d+)\.(\d+)", engine) if isinstance(engine, str) else None
    if match is None:
        return None
    return HoldRule(
        package=package,
        max_version=f"{match[1]}.{match[2]}",
        workspace=workspace_rel(ws),
        reason=f"vsce: @types/vscode may not exceed engines.vscode {engine}",
        source="vscode",
    )


_run_holds: dict[tuple[str, str], HoldRule] = {}


def hold_for_run(ws: Workspace, package: str, version: str, reason: str) -> None:
    """Cap *package* at *version* for *ws* (and its members) until :func:`release_run_hold`."""
    _run_holds[(str(ws.path), package)] = HoldRule(
        package=package,
        max_version=version,
        workspace=workspace_rel(ws),
        reason=reason,
        source="run",
    )


def release_run_hold(ws: Workspace, package: str) -> None:
    _run_holds.pop((str(ws.path), package), None)


def _run_hold(ws: Workspace, package: str) -> HoldRule | None:
    for (root, name), rule in list(_run_holds.items()):
        if name == package and ws.path.is_relative_to(root):
            return rule
    return None


def resolve_package_hold(ws: Workspace, package: str) -> HoldRule | None:
    """A derived ceiling (Expo SDK, ``engines.vscode``) when one governs *package*, else ``[[hold]]``.

    A hold this run placed itself (a bump that failed verify) comes first.

    A derived ceiling wins over a configured hold: the build accepts nothing past it,
    and ``expo.install.exclude`` is Expo's documented opt-out.
    """
    return (
        _run_hold(ws, package)
        or expo_hold(ws, package)
        or vscode_types_hold(ws, package)
        or core_holds.resolve_package_hold(ws, package)
    )


def hold_version_spec(rule: HoldRule) -> str:
    """npm-registry spec for a rule: its own install spec, else the ceiling (``pkg@"<=6"``)."""
    return rule.spec or f"<={rule.max_version}"


def ceiling_version(ws: Workspace, name: str, rule: HoldRule) -> str | None:
    """The highest published release *rule* allows, or None when the registry cannot say.

    A prerelease is never the answer: ``<=5`` would otherwise match ``5.0.0-rc.1``
    above nothing newer.
    """
    proc = run(
        ["npm", "view", f"{name}@{hold_version_spec(rule)}", "version", "--json"],
        ws.path,
        timeout=TIMEOUT_CHECK,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    found = [data] if isinstance(data, str) else data if isinstance(data, list) else []
    releases = [v for v in found if isinstance(v, str) and "-" not in v]
    return max(releases, key=semver_precedence, default=None)


def hold_install_spec(ws: Workspace, name: str, rule: HoldRule) -> str:
    """What to install to land on the ceiling, in the range the saved manifest should carry.

    The concrete release is always what lands. A major-level ceiling (``5``)
    installs ``^X.Y.Z``, a minor-level one (``5.4``) ``~X.Y.Z``, so the saved range
    cannot reach past the hold; a patch-level ceiling installs the bare release,
    which :func:`exact_install_flag` has the manager save exactly.
    A rule's own install spec wins; a registry that cannot answer falls back to
    the ``<=X`` range.
    """
    if rule.spec:
        return rule.spec
    release = ceiling_version(ws, name, rule)
    if release is None:
        return hold_version_spec(rule)
    width = len(rule.max_version.split("."))
    return f"^{release}" if width == 1 else f"~{release}" if width == 2 else release


def exact_install_flag(spec: str, flag: str) -> list[str]:
    """*flag* when *spec* is one bare release, so the manager saves it without ``^``."""
    return [flag] if re.fullmatch(r"\d+\.\d+\.\d+", spec) else []


def plan_held_changes(
    ws: Workspace, changes: list[Change], behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Show in a plan the version the hold ceiling allows, as apply will land it.

    A change whose target is over the ceiling becomes a move to the ceiling's
    release (or disappears when the package already sits there); a held
    behind-latest row stays in *behind* and also gains that move.
    """
    out: list[Change] = []
    for change in changes:
        rule = resolve_package_hold(ws, change.name)
        if rule is None or version_satisfies_ceiling(change.to.lstrip("v"), rule.max_version):
            out.append(change)
            continue
        out.extend(_clamped(ws, change, rule))
    for change in behind:
        rule = resolve_package_hold(ws, change.name)
        if rule is None or version_satisfies_ceiling(change.to.lstrip("v"), rule.max_version):
            continue
        if not any(c.name == change.name for c in out):
            out.extend(_clamped(ws, change, rule))
    return out, behind


def _clamped(ws: Workspace, change: Change, rule: HoldRule) -> list[Change]:
    target = ceiling_version(ws, change.name, rule)
    if target is None:
        return []
    try:
        if semver_precedence(target) <= semver_precedence(change.from_ver.lstrip("^~=v")):
            return []
    except (TypeError, ValueError):
        return []
    return [Change(change.name, change.from_ver, target)]


def direct_dependency_names(pkg_data: dict[str, object]) -> set[str]:
    """All direct dependency names from a parsed ``package.json``.

    Builds :func:`enforce_holds`' candidate set beyond just this
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


def _declared(ws: Workspace, name: str) -> str | None:
    """The release *name*'s ``package.json`` spec names (``^5.1.0`` gives ``5.1.0``), else None.

    A restored snapshot rewinds the manifest and lockfile but not ``node_modules``, so
    the installed copy can sit under a ceiling the manifest already crosses.
    """
    data = read_json_file(ws.path / "package.json") or {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps = data.get(section)
        spec = deps.get(name) if isinstance(deps, dict) else None
        match = re.search(r"\d+\.\d+\.\d+", spec) if isinstance(spec, str) else None
        if match:
            return match[0]
    return None


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
        declared = _declared(ws, name)
        over_ceiling = next(
            (
                v
                for v in (_resolved(ws, name), declared)
                if v is not None and not version_satisfies_ceiling(v, rule.max_version)
            ),
            None,
        )
        if over_ceiling is None:
            continue
        proc = run_install(name, hold_install_spec(ws, name, rule))
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
