"""Local smoke tests for repo-ops-py-pip-tools (F213)."""

from __future__ import annotations

from repo_ops_py_pip_tools import register
from repo_ops_py_pip_tools.pip_tools import apply_pip_tools, check_pip_tools


def test_register_exports_callable() -> None:
    assert callable(register)
    assert callable(check_pip_tools)
    assert callable(apply_pip_tools)
