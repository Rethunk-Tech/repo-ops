"""pipenv Python plugin."""

from __future__ import annotations

from repo_ops_py_pipenv.pipenv import apply_pipenv, check_pipenv
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("pipenv", check_pipenv, apply_pipenv)
