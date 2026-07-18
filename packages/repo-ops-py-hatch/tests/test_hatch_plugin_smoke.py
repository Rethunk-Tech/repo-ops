"""Local smoke tests for repo-ops-py-hatch (F213)."""

from __future__ import annotations

from repo_ops_py_hatch import register
from repo_ops_py_hatch.hatch import apply_hatch, check_hatch


def test_register_exports_callable() -> None:
    assert callable(register)
    assert callable(check_hatch)
    assert callable(apply_hatch)
