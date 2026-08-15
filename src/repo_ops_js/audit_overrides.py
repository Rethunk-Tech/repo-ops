"""Shared advisory-safe-version override store for the JS package managers.

Each ecosystem module owns its own ``_AUDIT_VERSION_OVERRIDES`` dict (set by
``repo_ops_audit``'s ``apply_remediations`` ahead of a targeted-bump pass, to
win over any version-ceiling hold on the same package) — this only
centralizes the mutation so each module's public setter stays a one-liner.
Mutates *store* in place rather than rebinding it, so the module-level dict
object stays the same one code elsewhere already holds a reference to.
"""

from __future__ import annotations


def set_audit_overrides(store: dict[str, str], overrides: dict[str, str]) -> None:
    store.clear()
    store.update(overrides)


def clear_audit_overrides(store: dict[str, str]) -> None:
    store.clear()
