"""hatch Python plugin."""

from __future__ import annotations

from repo_ops_py_hatch.hatch import apply_hatch, check_hatch
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("hatch", check_hatch, apply_hatch)
