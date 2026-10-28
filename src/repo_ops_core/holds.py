"""Package version-ceiling ("hold") rules — user-global, repo, and workspace scoped.

A hold rule caps a package at a maximum version, optionally until an
expiry date, so a specific major upgrade (e.g. TypeScript 7) can be held
back fleet-wide without holding back every
other package.

Config source: the ``hold`` array-of-tables key already returned by
``repo_ops_core.config.load_global_config`` (global,
``~/.config/repo-ops/config.toml``) and ``load_repo_config`` (per-repo,
``.repo-ops.toml``), both read-only; see ``config.py``.

    [[hold]]
    package = "typescript"      # exact name or fnmatch glob, e.g. "@types/*"
    max_version = "6"           # ceiling: "6" blocks major 7+, "5.9" blocks >5.9
    until = "2026-09-01"        # optional ISO date; omit = indefinite hold
    ecosystem = "node"          # optional filter (matches Workspace.ecosystem/.manager;
                                 # "js"/"javascript" and "python" are accepted aliases
                                 # for "node" and "py")
    workspace = "packages/legacy-app"  # optional fnmatch glob, relative to repo root
    reason = "TS7 breaking changes; wait for ecosystem to catch up"  # documentation only
"""

from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from repo_ops_core.config import load_global_config, load_repo_config
from repo_ops_core.version_utils import version_satisfies_ceiling


@dataclass(frozen=True)
class HoldRule:
    package: str
    max_version: str
    until: str | None = None
    ecosystem: str | None = None
    workspace: str | None = None
    reason: str | None = None
    source: str = "global"  # "global" | "repo" | "expo" | "vscode" — precedence + debugging only
    # Install spec to write instead of ``<=max_version`` when the source dictates one (Expo's bundled spec).
    spec: str | None = None


def _parse_hold_table(raw: Any, *, source: str) -> list[HoldRule]:
    """Parse a ``hold`` config value into rules, dropping malformed entries.

    Tolerant by design: an entry missing ``package`` or
    ``max_version``, or that isn't a table at all, is silently skipped
    rather than failing the whole run over one bad TOML entry.
    """
    if not isinstance(raw, list):
        return []
    rules: list[HoldRule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        package = str(entry.get("package") or "").strip()
        max_version = str(entry.get("max_version") or "").strip()
        if not package or not max_version:
            continue
        until = entry.get("until")
        ecosystem = entry.get("ecosystem")
        workspace = entry.get("workspace")
        reason = entry.get("reason")
        rules.append(
            HoldRule(
                package=package,
                max_version=max_version,
                until=str(until).strip() if until else None,
                ecosystem=str(ecosystem).strip() if ecosystem else None,
                workspace=str(workspace).strip() if workspace else None,
                reason=str(reason).strip() if reason else None,
                source=source,
            )
        )
    return rules


def load_global_holds() -> list[HoldRule]:
    """Load ``[[hold]]`` rules from the global config file."""
    return _parse_hold_table(load_global_config().get("hold"), source="global")


def load_repo_holds(repo_root: Path) -> list[HoldRule]:
    """Load ``[[hold]]`` rules from the per-repo config (pyproject/.repo-ops.toml)."""
    return _parse_hold_table(load_repo_config(repo_root).get("hold"), source="repo")


_warned_expired: set[tuple[str, str, str | None]] = set()


def _is_expired(rule: HoldRule, today: date) -> bool:
    if not rule.until:
        return False
    try:
        until_date = date.fromisoformat(rule.until)
    except ValueError:
        # Unparseable expiry: treat as non-expiring rather than silently
        # dropping the hold over a typo.
        return False
    expired = today > until_date
    if expired:
        key = (rule.package, rule.until, rule.workspace)
        if key not in _warned_expired:
            _warned_expired.add(key)
            print(
                f"repo-ops: hold on {rule.package!r} expired {rule.until} "
                "— treating as lifted (config file not modified)",
                file=sys.stderr,
            )
    return expired


def _matches_package(rule: HoldRule, package: str) -> bool:
    if rule.package == package:
        return True
    return fnmatch.fnmatch(package, rule.package)


# Discovery (repo_ops_upgrade/discovery.py) never assigns these — they're
# doc/config-authoring shorthand for the literal it does assign.
_ECOSYSTEM_ALIASES = {"js": "node", "javascript": "node", "python": "py"}

_KNOWN_ECOSYSTEMS = {
    "node",
    "go",
    "rust",
    "py",
    "deno",
    "container",
    "precommit",
    "actions",
    "submodule",
}
_KNOWN_MANAGERS = {
    "actions",
    "bun",
    "cargo",
    "container",
    "deno",
    "go",
    "npm",
    "pnpm",
    "precommit",
    "rust",
    "submodule",
    "yarn",
    "uv",
    "pdm",
    "pipenv",
    "pip-tools",
    "poetry",
    "hatch",
}

_warned_dead_filter: set[str] = set()


def _normalize_ecosystem(value: str) -> str:
    value = value.strip().lower()
    return _ECOSYSTEM_ALIASES.get(value, value)


def _matches_ecosystem(rule: HoldRule, ecosystem: str, manager: str) -> bool:
    if not rule.ecosystem:
        return True
    target = _normalize_ecosystem(rule.ecosystem)
    if target not in _KNOWN_ECOSYSTEMS and target not in _KNOWN_MANAGERS:
        if target not in _warned_dead_filter:
            _warned_dead_filter.add(target)
            print(
                f"repo-ops: hold ecosystem filter {rule.ecosystem!r} matches no "
                "known ecosystem or manager — this hold can never apply",
                file=sys.stderr,
            )
        return False
    return target in (_normalize_ecosystem(ecosystem), _normalize_ecosystem(manager))


def _matches_workspace(rule: HoldRule, workspace_rel: str) -> bool:
    if not rule.workspace:
        return True
    return fnmatch.fnmatch(workspace_rel, rule.workspace)


def resolve_hold(
    rules: list[HoldRule],
    *,
    package: str,
    ecosystem: str,
    manager: str,
    workspace_rel: str,
    today: date | None = None,
) -> HoldRule | None:
    """Return the most specific active hold rule for *package*, or ``None``.

    Matching: exact/glob package name, optional ecosystem filter (matches
    either the broad ecosystem, e.g. ``"node"``, or the specific manager,
    e.g. ``"npm"``; ``"js"``/``"javascript"`` and ``"python"`` are accepted
    aliases for ``"node"`` and ``"py"``), optional workspace glob (relative
    to the repo root), and not expired. Among matches, the most specific wins: a
    workspace-scoped rule beats a repo-wide rule, and a repo-sourced rule
    beats a global-sourced rule at equal specificity — so per-repo config
    can narrow *or* override a global hold for one workspace.
    """
    today = today or date.today()
    candidates = [
        rule
        for rule in rules
        if _matches_package(rule, package)
        and _matches_ecosystem(rule, ecosystem, manager)
        and _matches_workspace(rule, workspace_rel)
        and not _is_expired(rule, today)
    ]
    if not candidates:
        return None

    def specificity(rule: HoldRule) -> tuple[int, int]:
        return (1 if rule.workspace else 0, 1 if rule.source == "repo" else 0)

    candidates.sort(key=specificity, reverse=True)
    return candidates[0]


@cache
def _global_holds_cached() -> tuple[HoldRule, ...]:
    return tuple(load_global_holds())


@cache
def _repo_holds_cached(repo_root: str) -> tuple[HoldRule, ...]:
    return tuple(load_repo_holds(Path(repo_root)))


def workspace_rel(ws: Any) -> str:
    """Workspace path relative to its repo root, or ``"."`` at the root."""
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve()).as_posix()
    except ValueError:
        return "."
    return rel or "."


def resolve_package_hold(ws: Any, package: str) -> HoldRule | None:
    """Return the active hold rule for *package* in *ws*, or ``None``.

    Reads global + per-repo rules through a process-lifetime cache: every
    ecosystem plugin resolves holds per package across a whole sweep, so the
    config files would otherwise be re-read once per package per workspace.
    Call :func:`clear_hold_caches` after writing config in a test.

    ``ws`` is any object with ``path``, ``git_repo``, ``ecosystem`` and
    ``manager`` attributes (e.g. ``repo_ops_models.Workspace``).
    """
    repo_root = str(ws.git_repo.resolve())
    rules = [*_global_holds_cached(), *_repo_holds_cached(repo_root)]
    if not rules:
        return None
    return resolve_hold(
        rules,
        package=package,
        ecosystem=ws.ecosystem,
        manager=ws.manager,
        workspace_rel=workspace_rel(ws),
    )


def clear_hold_caches() -> None:
    """Drop the cached global/per-repo hold rules."""
    _global_holds_cached.cache_clear()
    _repo_holds_cached.cache_clear()


def target_within_hold(target: str, rule: HoldRule | None) -> bool:
    """Return whether *target* is allowed by *rule* (``None`` = no active hold)."""
    return rule is None or version_satisfies_ceiling(target.lstrip("v"), rule.max_version)
