"""Register all ecosystem plugins with the upgrade registry."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_precommit() -> None:
    """Register the pre-commit rev-pin checker and applier with the upgrade registry."""
    from repo_ops_upgrade import registry
    from repo_ops_upgrade.precommit import apply_precommit, check_precommit

    registry.register("precommit", check_precommit, apply_precommit)


def register_all() -> None:
    # Imports stay function-local: most CLI subcommands (doctor, install-skill,
    # license, sbom, dependabot) never touch the upgrade registry, and
    # this keeps them from paying for every ecosystem plugin's import.
    from repo_ops_container.container import apply_container, check_container
    from repo_ops_go.go import apply_go, check_go
    from repo_ops_js.deno import apply_deno, check_deno
    from repo_ops_js_bun.bun import apply_bun, check_bun
    from repo_ops_js_npm.npm import apply_npm, check_npm
    from repo_ops_js_pnpm.pnpm import apply_pnpm, check_pnpm
    from repo_ops_js_yarn.yarn import apply_yarn, check_yarn_snapshot
    from repo_ops_py_hatch.hatch import apply_hatch, check_hatch
    from repo_ops_py_pdm.pdm import apply_pdm, check_pdm
    from repo_ops_py_pip_tools.pip_tools import apply_pip_tools, check_pip_tools
    from repo_ops_py_pipenv.pipenv import apply_pipenv, check_pipenv
    from repo_ops_py_poetry.poetry import apply_poetry, check_poetry
    from repo_ops_py_uv.uv import apply_uv, check_uv
    from repo_ops_rust.rust import apply_rust, check_rust
    from repo_ops_upgrade import registry

    plugins = {
        "bun": (check_bun, apply_bun),
        "npm": (check_npm, apply_npm),
        "pnpm": (check_pnpm, apply_pnpm),
        "yarn": (check_yarn_snapshot, apply_yarn),
        "go": (check_go, apply_go),
        "rust": (check_rust, apply_rust),
        "uv": (check_uv, apply_uv),
        "poetry": (check_poetry, apply_poetry),
        "pip-tools": (check_pip_tools, apply_pip_tools),
        "hatch": (check_hatch, apply_hatch),
        "pdm": (check_pdm, apply_pdm),
        "pipenv": (check_pipenv, apply_pipenv),
        "deno": (check_deno, apply_deno),
        "container": (check_container, apply_container),
    }
    for name, (checker, applier) in plugins.items():
        registry.register(name, checker, applier)
    register_precommit()
