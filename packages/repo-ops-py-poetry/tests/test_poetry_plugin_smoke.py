"""Local smoke tests for repo-ops-py-poetry (F213)."""

from __future__ import annotations

from repo_ops_py_poetry import register
from repo_ops_py_poetry.poetry import apply_poetry, check_poetry


def test_register_exports_callable() -> None:
    assert callable(register)
    assert callable(check_poetry)
    assert callable(apply_poetry)
