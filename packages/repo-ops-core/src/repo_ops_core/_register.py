"""Registration helpers relocated to ``repo_ops._register``.

Core previously imported ``repo_ops_upgrade`` / ``repo_ops_py`` / ``repo_ops_js``
here to register precommit/pdm/pipenv/deno. That upward dependency is removed;
call ``repo_ops._register.register_all()`` (or the named helpers there) instead.
"""

from __future__ import annotations

_MOVED = frozenset(
    {
        "register_precommit",
        "register_pdm",
        "register_pipenv",
        "register_deno",
    }
)


def __getattr__(name: str) -> object:
    if name in _MOVED:
        raise AttributeError(
            f"{name} moved to repo_ops._register; import it from there "
            "(or call repo_ops._register.register_all())."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
