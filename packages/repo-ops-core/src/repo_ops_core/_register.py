"""Registration helpers for ecosystem plugins that are not yet in the main bundle.

Currently provides:

  ``register_precommit()``
    Registers the pre-commit rev-pin checker/applier with the upgrade
    registry.  Call this from ``repo_ops._register.register_all()``::

        from repo_ops_core._register import register_precommit
        register_precommit()

    Also add a ``consider_precommit(mdir)`` call inside
    ``repo_ops_upgrade.discovery.discover_workspaces_in_root`` (modelled on
    ``consider_container``) to surface pre-commit workspaces during discovery.
"""

from __future__ import annotations


def register_precommit() -> None:
    """Register the pre-commit rev-pin checker and applier with the upgrade registry."""
    from repo_ops_upgrade import registry
    from repo_ops_upgrade.precommit import apply_precommit, check_precommit

    registry.register("precommit", check_precommit, apply_precommit)
