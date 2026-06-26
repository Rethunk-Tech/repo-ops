"""Registration helpers for ecosystem plugins that are not yet in the main bundle.

Currently provides:

  ``register_precommit()``
    Registers the pre-commit rev-pin checker/applier with the upgrade registry.

  ``register_pdm()``
    Registers the PDM (Python Dependency Manager) checker/applier.

  ``register_pipenv()``
    Registers the pipenv checker/applier.

  ``register_deno()``
    Registers the Deno checker/applier.

Call each from ``repo_ops._register.register_all()``.
"""

from __future__ import annotations


def register_precommit() -> None:
    """Register the pre-commit rev-pin checker and applier with the upgrade registry."""
    from repo_ops_upgrade import registry
    from repo_ops_upgrade.precommit import apply_precommit, check_precommit

    registry.register("precommit", check_precommit, apply_precommit)


def register_pdm() -> None:
    """Register the PDM checker and applier with the upgrade registry."""
    from repo_ops_py.pdm import apply_pdm, check_pdm
    from repo_ops_upgrade import registry

    registry.register("pdm", check_pdm, apply_pdm)


def register_pipenv() -> None:
    """Register the pipenv checker and applier with the upgrade registry."""
    from repo_ops_py.pipenv import apply_pipenv, check_pipenv
    from repo_ops_upgrade import registry

    registry.register("pipenv", check_pipenv, apply_pipenv)


def register_deno() -> None:
    """Register the Deno checker and applier with the upgrade registry."""
    from repo_ops_js.deno import apply_deno, check_deno
    from repo_ops_upgrade import registry

    registry.register("deno", check_deno, apply_deno)
