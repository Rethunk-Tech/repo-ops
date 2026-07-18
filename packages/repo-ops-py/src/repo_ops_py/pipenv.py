"""pipenv check/apply — re-export from repo-ops-py-pipenv plugin package.

compat: kept so repo_ops_core._register.register_pipenv can import without a
workspace-root dependency list update (W4/W5 seam). Prefer
``from repo_ops_py_pipenv import register``.
"""

from __future__ import annotations

from repo_ops_py_pipenv.pipenv import apply_pipenv, check_pipenv

__all__ = ["apply_pipenv", "check_pipenv"]
