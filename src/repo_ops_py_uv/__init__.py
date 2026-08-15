"""uv Python plugin."""

from __future__ import annotations

from repo_ops_py_uv.uv import apply_uv, check_uv
from repo_ops_upgrade import registry


def register() -> None:
    registry.register("uv", check_uv, apply_uv)
