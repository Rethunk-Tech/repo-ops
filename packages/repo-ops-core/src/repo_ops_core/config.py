"""Layered settings for repo-ops: CLI flag > env var > config file > default.

Config files (TOML, read-only — never written):
  Global:  ~/.config/repo-ops/config.toml  (or $XDG_CONFIG_HOME/repo-ops/config.toml)
  Per-repo: <repo_root>/pyproject.toml [tool.repo-ops] table
            <repo_root>/.repo-ops.toml   flat keys (same names as above; pyproject wins)

Non-Python repos (no pyproject.toml) use the ``.repo-ops.toml`` sidecar file.
When both files exist, ``pyproject.toml``'s ``[tool.repo-ops]`` table takes precedence
over ``.repo-ops.toml`` on a per-key basis (pyproject value wins for any key present
in both files).

TOML parsing uses the stdlib ``tomllib`` on Python 3.11+, or the third-party
``tomli`` package on 3.10. When neither is available, config files are skipped
with a one-time warning (sidecar keys already loaded before a pyproject probe
are still returned — see ``load_repo_config``).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any, cast

from repo_ops_core.constants import (
    FIND_TIMEOUT,
    GIT_TIMEOUT,
    TIMEOUT_APPLY,
    TIMEOUT_CHECK,
    TIMEOUT_VERIFY,
)

_TOMLLIB_AVAILABLE = False
try:
    import tomllib as _tomllib

    _TOMLLIB_AVAILABLE = True
except ModuleNotFoundError:
    try:
        import tomli as _tomllib

        _TOMLLIB_AVAILABLE = True
    except ModuleNotFoundError:
        pass

# ---------------------------------------------------------------------------
# TOML loading helpers
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file; return empty dict on any error."""
    if not _TOMLLIB_AVAILABLE:
        return {}
    try:
        with path.open("rb") as fh:
            data: dict[str, Any] = _tomllib.load(fh)
            return data
    except Exception:
        return {}


_warned: bool = False


def _warn_no_tomllib_once() -> None:
    """Emit a one-time warning when tomllib is unavailable (Python < 3.11 + no tomli)."""
    global _warned
    if _warned:
        return
    _warned = True
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
    if not _TOMLLIB_AVAILABLE:
        _warn_no_tomllib_once()
        return {}
    return _load_toml(path)


def load_dot_repo_ops_config(repo_root: Path) -> dict[str, Any]:
    """Load flat keys from <repo_root>/.repo-ops.toml.

    Intended for non-Python repos that have no pyproject.toml.  The file uses
    the same key names as the ``[tool.repo-ops]`` table in pyproject.toml but
    at the top level (flat, no ``[tool.repo-ops]`` wrapper required).
    """
    sidecar = repo_root / ".repo-ops.toml"
    if not sidecar.is_file():
        return {}
    if not _TOMLLIB_AVAILABLE:
        _warn_no_tomllib_once()
        return {}
    return _load_toml(sidecar)


def load_repo_config(repo_root: Path) -> dict[str, Any]:
    """Load per-repo config from <repo_root>/pyproject.toml and/or .repo-ops.toml.

    Merge strategy: pyproject.toml ``[tool.repo-ops]`` table wins on a per-key
    basis over ``.repo-ops.toml``.  Both files are read and merged so that keys
    absent from pyproject.toml can still be supplied via the sidecar.
    """
    sidecar_cfg = load_dot_repo_ops_config(repo_root)

    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return sidecar_cfg
    if not _TOMLLIB_AVAILABLE:
        # pyproject cannot be parsed, but a sidecar already loaded above must
        # still apply (non-Python repos often use only .repo-ops.toml).
        _warn_no_tomllib_once()
        return sidecar_cfg
    raw = _load_toml(pyproject)
    tool_section: dict[str, Any] = cast(dict[str, Any], raw.get("tool", {}))
    pyproject_cfg: dict[str, Any] = cast(
        dict[str, Any], tool_section.get("repo-ops", {})
    )

    if not sidecar_cfg:
        return pyproject_cfg

    # Merge: sidecar as base, pyproject overrides per key.
    # For dict-valued keys (e.g. the ``verify`` sub-table) merge one level
    # deeper so that pyproject wins per sub-key rather than replacing the
    # whole sub-dict.
    merged: dict[str, Any] = dict(sidecar_cfg)
    for key, py_val in pyproject_cfg.items():
        sc_val = merged.get(key)
        if isinstance(py_val, dict) and isinstance(sc_val, dict):
            merged[key] = {**sc_val, **py_val}
        else:
            merged[key] = py_val
    return merged


# ---------------------------------------------------------------------------
# Settings resolution
# ---------------------------------------------------------------------------

# Maps setting name → (env_var, config_key, default).
# Timeout defaults match repo_ops_core.constants (single source of truth).
_SETTINGS_MAP: dict[str, tuple[str | None, str, Any]] = {
    # Execution
    "jobs": (None, "jobs", None),  # resolved to min(16, cpu_count) when None
    # Upgrade policy
    "go_upgrade_mode": ("REPO_OPS_GO_UPGRADE_MODE", "go_upgrade_mode", None),
    "actions_lint_level": ("REPO_OPS_ACTIONS_LINT_LEVEL", "actions_lint_level", None),
    # Timeouts (defaults from constants — same values resolve_timeout_* use)
    "timeout_check": ("REPO_OPS_TIMEOUT_CHECK", "timeout_check", TIMEOUT_CHECK),
    "timeout_apply": ("REPO_OPS_TIMEOUT_APPLY", "timeout_apply", TIMEOUT_APPLY),
    "timeout_verify": ("REPO_OPS_TIMEOUT_VERIFY", "timeout_verify", TIMEOUT_VERIFY),
    "find_timeout": ("REPO_OPS_FIND_TIMEOUT", "find_timeout", FIND_TIMEOUT),
    "git_timeout": ("REPO_OPS_GIT_TIMEOUT", "git_timeout", GIT_TIMEOUT),
    # Feature flags
    "bun_update_latest": ("REPO_OPS_BUN_UPDATE_LATEST", "bun_update_latest", None),
    "pnpm_update_latest": ("REPO_OPS_PNPM_UPDATE_LATEST", "pnpm_update_latest", None),
    "yarn_update_latest": ("REPO_OPS_YARN_UPDATE_LATEST", "yarn_update_latest", None),
    "yarn_targeted_bump": ("REPO_OPS_YARN_TARGETED_BUMP", "yarn_targeted_bump", None),
    "yarn_max_concurrency": (
        "REPO_OPS_YARN_MAX_CONCURRENCY",
        "yarn_max_concurrency",
        None,
    ),
    "npm_targeted_bump": ("REPO_OPS_NPM_TARGETED_BUMP", "npm_targeted_bump", None),
    "pnpm_targeted_bump": ("REPO_OPS_PNPM_TARGETED_BUMP", "pnpm_targeted_bump", None),
    "bun_targeted_bump": ("REPO_OPS_BUN_TARGETED_BUMP", "bun_targeted_bump", None),
    "go_work_sync": ("REPO_OPS_GO_WORK_SYNC", "go_work_sync", None),
    "no_progress": ("REPO_OPS_NO_PROGRESS", "no_progress", None),
    "no_state": ("REPO_OPS_NO_STATE", "no_state", None),
    "no_lint_cache": ("REPO_OPS_NO_LINT_CACHE", "no_lint_cache", None),
    "go_directive": ("REPO_OPS_GO_DIRECTIVE", "go_directive", None),
    "go_toolchain": ("REPO_OPS_GO_TOOLCHAIN", "go_toolchain", None),
    "node_verify_script": ("REPO_OPS_NODE_VERIFY_SCRIPT", "node_verify_script", None),
    "commit_agent_provider": (
        "REPO_OPS_COMMIT_AGENT_PROVIDER",
        "commit_agent_provider",
        None,
    ),
    "commit_umbrella": ("REPO_OPS_COMMIT_UMBRELLA", "commit_umbrella", None),
    "actions_org_policy": ("REPO_OPS_ACTIONS_ORG_POLICY", "actions_org_policy", None),
    "container_update_latest": (
        "REPO_OPS_CONTAINER_UPDATE_LATEST",
        "container_update_latest",
        None,
    ),
    # Apply resume checkpoint (apply-only): skip repos already applied in a prior run.
    "resume": ("REPO_OPS_RESUME", "resume", None),
    # Submodule tag-advance: allow cross-major semver tag bumps on submodule pointers.
    "submodule_update_latest": (
        "REPO_OPS_SUBMODULE_UPDATE_LATEST",
        "submodule_update_latest",
        None,
    ),
    # Pre-commit hook upgrades: allow cross-major rev bumps.
    "precommit_update_latest": (
        "REPO_OPS_PRECOMMIT_UPDATE_LATEST",
        "precommit_update_latest",
        None,
    ),
    # Container ecosystem: refresh image digest for SHA-pinned references.
    "container_refresh_sha": (
        "REPO_OPS_CONTAINER_REFRESH_SHA",
        "container_refresh_sha",
        None,
    ),
    # Hatch: opt in to creating uv.lock (also settable via --migrate-to-uv-lock flag).
    "hatch_migrate_uv_lock": (
        "REPO_OPS_HATCH_MIGRATE_UV_LOCK",
        "hatch_migrate_uv_lock",
        None,
    ),
    # Exclude patterns: CLI --exclude / config key ``exclude`` (list). No env
    # channel — lists do not serialize cleanly; run.py also special-cases merge.
    "exclude_patterns": (None, "exclude", None),
    # Audit fail-on gates: readable from [tool.repo-ops] so CI can pin the
    # severity threshold without repeating --fail-on / --fail-on-unknown on
    # every invocation. Consumed by repo_ops_audit's main() (CLI flag > env >
    # config > default); repo_ops_core only owns the settings-table rows.
    "audit_fail_on": ("REPO_OPS_AUDIT_FAIL_ON", "audit_fail_on", None),
    "audit_fail_on_unknown": (
        "REPO_OPS_AUDIT_FAIL_ON_UNKNOWN",
        "audit_fail_on_unknown",
        None,
    ),
}

# Public table for documentation generation.
SETTINGS_TABLE: list[tuple[str, str, str, str, str]] = [
    # (setting, flag, env_var, config_key, default)
    ("jobs", "--jobs", "(none)", "jobs", "min(16, cpu_count)"),
    (
        "yarn_max_concurrency",
        "--yarn-jobs",
        "REPO_OPS_YARN_MAX_CONCURRENCY",
        "yarn_max_concurrency",
        "2",
    ),
    (
        "go_upgrade_mode",
        "--go-upgrade-mode",
        "REPO_OPS_GO_UPGRADE_MODE",
        "go_upgrade_mode",
        "patch",
    ),
    (
        "actions_lint_level",
        "--actions-lint-level",
        "REPO_OPS_ACTIONS_LINT_LEVEL",
        "actions_lint_level",
        "block",
    ),
    ("timeout_check", "(none)", "REPO_OPS_TIMEOUT_CHECK", "timeout_check", "60"),
    ("timeout_apply", "(none)", "REPO_OPS_TIMEOUT_APPLY", "timeout_apply", "60"),
    ("timeout_verify", "(none)", "REPO_OPS_TIMEOUT_VERIFY", "timeout_verify", "60"),
    ("find_timeout", "(none)", "REPO_OPS_FIND_TIMEOUT", "find_timeout", "60"),
    ("git_timeout", "(none)", "REPO_OPS_GIT_TIMEOUT", "git_timeout", "10"),
    (
        "bun_update_latest",
        "--latest",
        "REPO_OPS_BUN_UPDATE_LATEST",
        "bun_update_latest",
        "0",
    ),
    (
        "pnpm_update_latest",
        "--latest",
        "REPO_OPS_PNPM_UPDATE_LATEST",
        "pnpm_update_latest",
        "0",
    ),
    (
        "yarn_update_latest",
        "--latest",
        "REPO_OPS_YARN_UPDATE_LATEST",
        "yarn_update_latest",
        "0",
    ),
    (
        "yarn_targeted_bump",
        "--yarn-targeted-bump",
        "REPO_OPS_YARN_TARGETED_BUMP",
        "yarn_targeted_bump",
        "0",
    ),
    (
        "npm_targeted_bump",
        "--npm-targeted-bump",
        "REPO_OPS_NPM_TARGETED_BUMP",
        "npm_targeted_bump",
        "0",
    ),
    (
        "pnpm_targeted_bump",
        "--pnpm-targeted-bump",
        "REPO_OPS_PNPM_TARGETED_BUMP",
        "pnpm_targeted_bump",
        "0",
    ),
    (
        "bun_targeted_bump",
        "--bun-targeted-bump",
        "REPO_OPS_BUN_TARGETED_BUMP",
        "bun_targeted_bump",
        "0",
    ),
    ("go_work_sync", "(none)", "REPO_OPS_GO_WORK_SYNC", "go_work_sync", "0"),
    ("no_progress", "(none)", "REPO_OPS_NO_PROGRESS", "no_progress", "0"),
    ("no_state", "(none)", "REPO_OPS_NO_STATE", "no_state", "0"),
    ("no_lint_cache", "(none)", "REPO_OPS_NO_LINT_CACHE", "no_lint_cache", "0"),
    ("go_directive", "(none)", "REPO_OPS_GO_DIRECTIVE", "go_directive", "(none)"),
    ("go_toolchain", "(none)", "REPO_OPS_GO_TOOLCHAIN", "go_toolchain", "(none)"),
    (
        "node_verify_script",
        "(none)",
        "REPO_OPS_NODE_VERIFY_SCRIPT",
        "node_verify_script",
        "(none)",
    ),
    (
        "commit_agent_provider",
        "(none)",
        "REPO_OPS_COMMIT_AGENT_PROVIDER",
        "commit_agent_provider",
        "cursor",
    ),
    ("exclude_patterns", "--exclude", "(none)", "exclude", "[]"),
    (
        "commit_umbrella",
        "--commit-umbrella",
        "REPO_OPS_COMMIT_UMBRELLA",
        "commit_umbrella",
        "0",
    ),
    (
        "actions_org_policy",
        "--actions-org-policy",
        "REPO_OPS_ACTIONS_ORG_POLICY",
        "actions_org_policy",
        "0",
    ),
    (
        "container_update_latest",
        "--container-update-latest",
        "REPO_OPS_CONTAINER_UPDATE_LATEST",
        "container_update_latest",
        "0",
    ),
    ("resume", "(none)", "REPO_OPS_RESUME", "resume", "0"),
    (
        "submodule_update_latest",
        "(none)",
        "REPO_OPS_SUBMODULE_UPDATE_LATEST",
        "submodule_update_latest",
        "0",
    ),
    (
        "precommit_update_latest",
        "(none)",
        "REPO_OPS_PRECOMMIT_UPDATE_LATEST",
        "precommit_update_latest",
        "0",
    ),
    (
        "container_refresh_sha",
        "--container-refresh-sha",
        "REPO_OPS_CONTAINER_REFRESH_SHA",
        "container_refresh_sha",
        "0",
    ),
    (
        "hatch_migrate_uv_lock",
        "--migrate-to-uv-lock",
        "REPO_OPS_HATCH_MIGRATE_UV_LOCK",
        "hatch_migrate_uv_lock",
        "0",
    ),
    (
        "audit_fail_on",
        "--fail-on",
        "REPO_OPS_AUDIT_FAIL_ON",
        "audit_fail_on",
        "high",
    ),
    (
        "audit_fail_on_unknown",
        "--fail-on-unknown",
        "REPO_OPS_AUDIT_FAIL_ON_UNKNOWN",
        "audit_fail_on_unknown",
        "0",
    ),
]


def apply_config_to_env(
    *,
    global_cfg: dict[str, Any] | None = None,
    repo_cfg: dict[str, Any] | None = None,
) -> None:
    """Inject config-file values into ``os.environ`` for env-var-backed settings.

    Called once at process start (by ``run.py`` after arg parsing).  Skips any
    env var that is already present in the environment (user shell or a CLI-flag
    path that ran first) so the precedence ``CLI > env > config > default`` is
    preserved.

    Boolean config values (TOML ``true`` / ``false``) are converted to ``"1"``
    / ``"0"`` so the existing ``== "1"`` readers in checker modules work
    unchanged.  Repo config wins over global config on a per-key basis.
    """
    for _name, (_env_var, _config_key, _) in _SETTINGS_MAP.items():
        if _env_var is None:
            continue  # no env-var channel (e.g. ``jobs``) — caller handles separately
        if _env_var in os.environ:
            continue  # user shell or earlier CLI wiring already set it
        _val: Any = None
        if repo_cfg and _config_key in repo_cfg:
            _val = repo_cfg[_config_key]
        elif global_cfg and _config_key in global_cfg:
            _val = global_cfg[_config_key]
        if _val is None:
            continue
        if _val is True:
            os.environ[_env_var] = "1"
        elif _val is False:
            os.environ[_env_var] = "0"
        else:
            os.environ[_env_var] = str(_val)


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
