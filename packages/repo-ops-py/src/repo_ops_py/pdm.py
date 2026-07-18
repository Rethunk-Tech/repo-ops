"""PDM check/apply — re-export from repo-ops-py-pdm plugin package.

compat: kept so repo_ops_core._register.register_pdm can import without a
workspace-root dependency list update (W4/W5 seam). Prefer
``from repo_ops_py_pdm import register``.
"""

from __future__ import annotations

from repo_ops_py_pdm.pdm import apply_pdm, check_pdm

__all__ = ["apply_pdm", "check_pdm"]
