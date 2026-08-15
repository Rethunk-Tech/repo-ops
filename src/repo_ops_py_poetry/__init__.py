"""poetry Python plugin."""

from __future__ import annotations

from repo_ops_py_poetry.poetry import apply_poetry, check_poetry
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("poetry", check_poetry, apply_poetry)
