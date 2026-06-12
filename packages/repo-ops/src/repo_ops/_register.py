"""Register all ecosystem plugins with the upgrade registry."""

from __future__ import annotations


def register_all() -> None:
    from repo_ops_container import register as register_container
    from repo_ops_go import register as register_go
    from repo_ops_js_bun import register as register_bun
    from repo_ops_js_npm import register as register_npm
    from repo_ops_js_pnpm import register as register_pnpm
    from repo_ops_js_yarn import register as register_yarn
    from repo_ops_py_hatch import register as register_hatch
    from repo_ops_py_pip_tools import register as register_pip_tools
    from repo_ops_py_poetry import register as register_poetry
    from repo_ops_py_uv import register as register_uv
    from repo_ops_rust import register as register_rust

    register_bun()
    register_npm()
    register_pnpm()
    register_yarn()
    register_go()
    register_rust()
    register_uv()
    register_poetry()
    register_pip_tools()
    register_hatch()
    register_container()
