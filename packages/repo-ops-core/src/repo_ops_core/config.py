"""Layered settings for repo-ops: CLI flag > env var > config file > default.

Config files (TOML, read-only — never written):
  Global:  ~/.config/repo-ops/config.toml  (or $XDG_CONFIG_HOME/repo-ops/config.toml)
  Per-repo: <repo_root>/pyproject.toml [tool.repo-ops] table

Python 3.11+ only; skipped with a warning on 3.10 unless ``tomli`` is installed.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# TOML loading helpers
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file; return empty dict on any error."""
    if tomllib is None:
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def _warn_no_tomllib_once() -> None:
    """Emit a one-time warning when tomllib is unavailable (Python < 3.11 + no tomli)."""
    warnings.warn(
        "repo-ops config file support requires Python 3.11+ (or the 'tomli' package on 3.10); "
        "config files will be ignored for this run.",
        stacklevel=3,
    )


# ---------------------------------------------------------------------------
# Config path helpers
# ---------------------------------------------------------------------------


def _global_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "repo-ops" / "config.toml"


def load_global_config() -> dict[str, Any]:
    """Load ~/.config/repo-ops/config.toml (or $XDG_CONFIG_HOME/repo-ops/config.toml)."""
    path = _global_config_path()
    if not path.is_file():
        return {}
    if tomllib is None:
        _warn_no_tomllib_once()
        return {}
    return _load_toml(path)


def load_repo_config(repo_root: Path) -> dict[str, Any]:
    """Load [tool.repo-ops] from <repo_root>/pyproject.toml."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    if tomllib is None:
        _warn_no_tomllib_once()
        return {}
    raw = _load_toml(pyproject)
    return raw.get("tool", {}).get("repo-ops", {})


# ---------------------------------------------------------------------------
# Settings resolution
# ---------------------------------------------------------------------------

# Maps setting name → (env_var, config_key, default)
_SETTINGS_MAP: dict[str, tuple[str | None, str, Any]] = {
    # Execution
    "jobs": (None, "jobs", None),  # resolved to min(16, cpu_count) when None
    # Upgrade policy
    "go_upgrade_mode": ("REPO_OPS_GO_UPGRADE_MODE", "go_upgrade_mode", None),
    "actions_lint_level": ("REPO_OPS_ACTIONS_LINT_LEVEL", "actions_lint_level", None),
    # Timeouts
    "timeout_check": ("REPO_OPS_TIMEOUT_CHECK", "timeout_check", None),
    "timeout_apply": ("REPO_OPS_TIMEOUT_APPLY", "timeout_apply", None),
    "timeout_verify": ("REPO_OPS_TIMEOUT_VERIFY", "timeout_verify", None),
    "find_timeout": ("REPO_OPS_FIND_TIMEOUT", "find_timeout", None),
    # Feature flags
    "bun_update_latest": ("REPO_OPS_BUN_UPDATE_LATEST", "bun_update_latest", None),
    "pnpm_update_latest": ("REPO_OPS_PNPM_UPDATE_LATEST", "pnpm_update_latest", None),
    "yarn_update_latest": ("REPO_OPS_YARN_UPDATE_LATEST", "yarn_update_latest", None),
    "yarn_targeted_bump": ("REPO_OPS_YARN_TARGETED_BUMP", "yarn_targeted_bump", None),
    "go_work_sync": ("REPO_OPS_GO_WORK_SYNC", "go_work_sync", None),
    "no_progress": ("REPO_OPS_NO_PROGRESS", "no_progress", None),
    "node_verify_script": ("REPO_OPS_NODE_VERIFY_SCRIPT", "node_verify_script", None),
    "commit_agent_provider": ("REPO_OPS_COMMIT_AGENT_PROVIDER", "commit_agent_provider", None),
}

# Public table for documentation generation.
SETTINGS_TABLE: list[tuple[str, str, str, str, str]] = [
    # (setting, flag, env_var, config_key, default)
    ("jobs", "--jobs", "(none)", "jobs", "min(16, cpu_count)"),
    ("go_upgrade_mode", "--go-upgrade-mode", "REPO_OPS_GO_UPGRADE_MODE", "go_upgrade_mode", "patch"),
    ("actions_lint_level", "--actions-lint-level", "REPO_OPS_ACTIONS_LINT_LEVEL", "actions_lint_level", "block"),
    ("timeout_check", "(none)", "REPO_OPS_TIMEOUT_CHECK", "timeout_check", "60"),
    ("timeout_apply", "(none)", "REPO_OPS_TIMEOUT_APPLY", "timeout_apply", "300"),
    ("timeout_verify", "(none)", "REPO_OPS_TIMEOUT_VERIFY", "timeout_verify", "120"),
    ("find_timeout", "(none)", "REPO_OPS_FIND_TIMEOUT", "find_timeout", "30"),
    ("bun_update_latest", "--latest", "REPO_OPS_BUN_UPDATE_LATEST", "bun_update_latest", "0"),
    ("pnpm_update_latest", "--latest", "REPO_OPS_PNPM_UPDATE_LATEST", "pnpm_update_latest", "0"),
    ("yarn_update_latest", "--latest", "REPO_OPS_YARN_UPDATE_LATEST", "yarn_update_latest", "0"),
    ("yarn_targeted_bump", "--yarn-targeted-bump", "REPO_OPS_YARN_TARGETED_BUMP", "yarn_targeted_bump", "0"),
    ("go_work_sync", "(none)", "REPO_OPS_GO_WORK_SYNC", "go_work_sync", "0"),
    ("no_progress", "(none)", "REPO_OPS_NO_PROGRESS", "no_progress", "0"),
    ("node_verify_script", "(none)", "REPO_OPS_NODE_VERIFY_SCRIPT", "node_verify_script", "(none)"),
    ("commit_agent_provider", "(none)", "REPO_OPS_COMMIT_AGENT_PROVIDER", "commit_agent_provider", "cursor"),
    ("exclude_patterns", "--exclude", "(none)", "exclude", "[]"),
]


def resolve_setting(
    name: str,
    *,
    cli_value: Any = None,
    global_cfg: dict[str, Any] | None = None,
    repo_cfg: dict[str, Any] | None = None,
) -> Any:
    """Resolve one setting using priority: CLI > env > config > default.

    Parameters
    ----------
    name:
        Setting name (key in ``_SETTINGS_MAP``).
    cli_value:
        Parsed CLI value. ``None`` means the flag was not supplied (use lower layers).
    global_cfg:
        Pre-loaded global config dict (avoids re-reading the file per call).
    repo_cfg:
        Pre-loaded per-repo config dict.
    """
    entry = _SETTINGS_MAP.get(name)
    if entry is None:
        raise KeyError(f"unknown setting: {name!r}")
    env_var, config_key, default = entry

    if cli_value is not None:
        return cli_value
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val is not None:
            return env_val
    if repo_cfg and config_key in repo_cfg:
        return repo_cfg[config_key]
    if global_cfg and config_key in global_cfg:
        return global_cfg[config_key]
    return default
