"""pip-tools Python plugin."""

from __future__ import annotations

from repo_ops_upgrade import registry

from repo_ops_py_pip_tools.pip_tools import apply_pip_tools, check_pip_tools


def register() -> None:
    registry.register("pip-tools", check_pip_tools, apply_pip_tools)
