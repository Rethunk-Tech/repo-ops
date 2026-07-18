"""Local smoke tests for repo-ops-py-uv (F213)."""

from __future__ import annotations

from repo_ops_py_uv import register
from repo_ops_py_uv.uv import apply_uv, check_uv


def test_register_exports_callable() -> None:
    assert callable(register)
    assert callable(check_uv)
    assert callable(apply_uv)
