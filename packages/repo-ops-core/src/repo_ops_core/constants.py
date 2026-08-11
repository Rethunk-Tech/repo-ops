"""Shared defaults for repo-ops commands."""

from __future__ import annotations

import os
from pathlib import Path

SKIP_DIRS = {
    "node_modules",
    ".git",
    ".next",
    "dist",
    "build",
    "out",
    ".turbo",
    ".cache",
    "vendor",
    "third_party",
    ".venv",
    "venv",
    ".claude",
    "target",
    ".yarn",
    "coverage",
    ".nuxt",
    ".svelte-kit",
    ".worktrees",
    "wailsjs",
}

EXCLUDE_PATH_FRAGMENTS = (
    "/.cache/",
    "/pre-commit/",
    "/.cursor/plugins/",
    "/.claude/plugins/",
    "/node_modules/",
    "/.oh-my-",
    "/chunkhound",
    "/.vscode",
    "/.local/share/tmux/",
    "/.cargo/registry/",
    "/go/pkg/mod/",
    "/snap/",
    "/vendor/",
)

DEFAULT_ROOTS = [
    "/usr/local/src",
    str(Path.home() / "Projects"),
    str(Path.home() / "Documents"),
    str(Path.home() / "bastion-ai-helpers"),
    str(Path.home() / "Apps"),
]

# ---------------------------------------------------------------------------
# Env-overridable timeout resolvers.
# Shared across all ecosystem plugins (uv, rust, go, yarn, bun, poetry, …).
# Canonical defaults are 60 s (aligned with SETTINGS_TABLE / resolve_setting).
# ---------------------------------------------------------------------------

TIMEOUT_CHECK = 60
TIMEOUT_APPLY = 60
TIMEOUT_VERIFY = 60
FIND_TIMEOUT = 60
GIT_TIMEOUT = 10

_TIMEOUT_CHECK_ENV = "REPO_OPS_TIMEOUT_CHECK"
_TIMEOUT_APPLY_ENV = "REPO_OPS_TIMEOUT_APPLY"
_TIMEOUT_VERIFY_ENV = "REPO_OPS_TIMEOUT_VERIFY"
_FIND_TIMEOUT_ENV = "REPO_OPS_FIND_TIMEOUT"
_GIT_TIMEOUT_ENV = "REPO_OPS_GIT_TIMEOUT"


def _resolve_timeout(setting: str, env_var: str, default: int) -> int:
    """Resolve a timeout from the active checkout and ambient environment."""
    from repo_ops_core.config import (
        active_checkout_config,
        ambient_env,
        load_global_config,
        resolve_setting,
    )

    config_layers = active_checkout_config()
    global_cfg, repo_cfg = (
        config_layers if config_layers is not None else (load_global_config(), {})
    )
    ambient = ambient_env()
    raw = resolve_setting(
        setting,
        global_cfg=global_cfg,
        repo_cfg=repo_cfg,
        env=ambient,
    )
    if raw is None and ambient is None:
        raw = os.environ.get(env_var, "")
    raw = str(raw).strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return int(value)
        except ValueError:
            pass
    return default


def resolve_timeout_check() -> int:
    """Return TIMEOUT_CHECK, overridable via ``REPO_OPS_TIMEOUT_CHECK``."""
    return _resolve_timeout("timeout_check", _TIMEOUT_CHECK_ENV, TIMEOUT_CHECK)


def resolve_timeout_apply() -> int:
    """Return TIMEOUT_APPLY, overridable via ``REPO_OPS_TIMEOUT_APPLY``."""
    return _resolve_timeout("timeout_apply", _TIMEOUT_APPLY_ENV, TIMEOUT_APPLY)


def resolve_timeout_verify() -> int:
    """Return TIMEOUT_VERIFY, overridable via ``REPO_OPS_TIMEOUT_VERIFY``."""
    return _resolve_timeout("timeout_verify", _TIMEOUT_VERIFY_ENV, TIMEOUT_VERIFY)


def resolve_find_timeout() -> float:
    """Return FIND_TIMEOUT, overridable via ``REPO_OPS_FIND_TIMEOUT``."""
    return float(_resolve_timeout("find_timeout", _FIND_TIMEOUT_ENV, FIND_TIMEOUT))


def resolve_git_timeout() -> float:
    """Return GIT_TIMEOUT, overridable via ``REPO_OPS_GIT_TIMEOUT``."""
    return float(_resolve_timeout("git_timeout", _GIT_TIMEOUT_ENV, GIT_TIMEOUT))
