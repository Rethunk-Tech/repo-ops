"""PDM Python plugin."""

from __future__ import annotations

from repo_ops_py_pdm.pdm import apply_pdm, check_pdm
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("pdm", check_pdm, apply_pdm)
