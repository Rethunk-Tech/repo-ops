"""Local smoke tests for repo-ops-py-pipenv (F213)."""

from __future__ import annotations

from repo_ops_py_pipenv import register
from repo_ops_py_pipenv.pipenv import apply_pipenv, check_pipenv


def test_register_exports_callable() -> None:
    assert callable(register)
    assert callable(check_pipenv)
    assert callable(apply_pipenv)
