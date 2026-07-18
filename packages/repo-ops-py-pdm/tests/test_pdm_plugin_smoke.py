"""Local smoke tests for repo-ops-py-pdm (F213)."""

from __future__ import annotations

from repo_ops_py_pdm import register
from repo_ops_py_pdm.pdm import apply_pdm, check_pdm


def test_register_exports_callable() -> None:
    assert callable(register)
    assert callable(check_pdm)
    assert callable(apply_pdm)
