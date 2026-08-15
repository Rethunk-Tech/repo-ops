"""uv Python plugin."""

from __future__ import annotations

from repo_ops_py_uv.uv import apply_uv, check_uv
from repo_ops_upgrade import registry


def register() -> None:
    # repo_ops_audit.audit._try_register_manager imports this to register a
    # single manager on demand without pulling in every ecosystem plugin.
    registry.register("uv", check_uv, apply_uv)
