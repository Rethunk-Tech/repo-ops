"""Shared advisory-safe-version override store for the JS package managers.

Each ecosystem module owns its own ``_AUDIT_VERSION_OVERRIDES`` dict (set by
``repo_ops_audit``'s ``apply_remediations`` ahead of a targeted-bump pass, to
win over any version-ceiling hold on the same package) — this only
centralizes the mutation so each module's public setter stays a one-liner.
Mutates *store* in place rather than rebinding it, so the module-level dict
object stays the same one code elsewhere already holds a reference to.
"""

from __future__ import annotations

from collections.abc import Callable

from repo_ops_js.guards import assert_targets_resolved
from repo_ops_models import Change, Workspace


def set_audit_overrides(store: dict[str, str], overrides: dict[str, str]) -> None:
    store.clear()
    store.update(overrides)


def clear_audit_overrides(store: dict[str, str]) -> None:
    store.clear()


def make_audit_override_api(
    store: dict[str, str],
) -> tuple[Callable[[dict[str, str]], None], Callable[[], None]]:
    """Return the bound ``(set_overrides, clear_overrides)`` pair for *store*.

    Each ecosystem module still exposes its own ``set_<manager>_...`` /
    ``clear_<manager>_...`` public names (``repo_ops_audit.audit`` resolves
    them by name via ``getattr``) — this just gives them one shared body
    instead of a copy-pasted one-liner apiece.
    """

    def set_overrides(overrides: dict[str, str]) -> None:
        set_audit_overrides(store, overrides)

    def clear_overrides() -> None:
        clear_audit_overrides(store)

    return set_overrides, clear_overrides


def apply_audit_overrides(
    ws: Workspace,
    overrides: dict[str, str],
    *,
    manager: str,
    install: Callable[[str, str], None],
    guard: bool = True,
) -> set[str]:
    """Install every advisory-safe pin in *overrides*, then guard once.

    *install* performs the manager-specific install for one package and
    raises on failure — each manager keeps its own error-message shape.
    Independent of the targeted-bump flag so an ``audit --fix`` pin is never
    silently skipped when the operator didn't also opt into targeted bump.
    ``guard`` lets a caller skip the post-install ghost-bump check when it
    cannot be evaluated (e.g. a yarn Plug'n'Play workspace with no
    ``node_modules``).
    """
    handled: set[str] = set()
    if not overrides:
        return handled
    requested: list[Change] = []
    for name, safe_version in list(overrides.items()):
        name = (name or "").strip()
        safe_version = (safe_version or "").strip()
        if not name or not safe_version:
            continue
        install(name, safe_version)
        requested.append(Change(name, "", safe_version))
        handled.add(name)
    if requested and guard:
        assert_targets_resolved(ws, requested, manager=manager)
    return handled
