"""Layered settings for repo-ops: CLI flag > env var > config file > default.

Config files (TOML, read-only — never written):
  Global:  ~/.config/repo-ops/config.toml  (or $XDG_CONFIG_HOME/repo-ops/config.toml)
  Per-repo: <repo_root>/pyproject.toml [tool.repo-ops] table
            <repo_root>/.repo-ops.toml   flat keys (same names as above; pyproject wins)

Non-Python repos (no pyproject.toml) use the ``.repo-ops.toml`` sidecar file.
When both files exist, ``pyproject.toml``'s ``[tool.repo-ops]`` table takes precedence
over ``.repo-ops.toml`` on a per-key basis (pyproject value wins for any key present
in both files).

Package version-ceiling ("hold") rules use the same global/repo files via a
``[[hold]]`` array-of-tables key, resolved by ``repo_ops_core.holds`` — see
that module's docstring for the rule schema and precedence.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from repo_ops_core.constants import (
    FIND_TIMEOUT,
    GIT_TIMEOUT,
    TIMEOUT_APPLY,
    TIMEOUT_CHECK,
    TIMEOUT_VERIFY,
)
from repo_ops_core.filesystem import read_toml_file

# ---------------------------------------------------------------------------
# TOML loading helpers
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file; return empty dict on any error."""
    return read_toml_file(path) or {}


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
    raw = _load_toml(pyproject)
    tool_section: dict[str, Any] = cast(dict[str, Any], raw.get("tool", {}))
    pyproject_cfg: dict[str, Any] = cast(dict[str, Any], tool_section.get("repo-ops", {}))

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


@dataclass(frozen=True)
class _Setting:
    """One row driving both settings resolution and doc/report output.

    ``default`` is what ``resolve_setting`` falls back to at runtime (often
    ``None`` because the real default is computed elsewhere, e.g.
    ``min(16, cpu_count)``); ``default_doc`` is the human-readable default
    shown in ``SETTINGS_TABLE`` output and can differ from ``default`` for
    exactly that reason.
    """

    name: str
    env_var: str | None
    config_key: str
    default: Any
    flag: str
    default_doc: str


class _Derive:
    """Sentinel: derive this field from ``name`` in :func:`_s` unless overridden."""


_DERIVE = _Derive()


def _s(
    name: str,
    default: Any,
    flag: str,
    default_doc: str,
    *,
    env_var: str | _Derive | None = _DERIVE,
    config_key: str | _Derive = _DERIVE,
) -> _Setting:
    """Build one settings-table row.

    ``env_var`` defaults to ``REPO_OPS_<NAME>`` and ``config_key`` defaults to
    ``name`` — true for all but two settings — so a row is one line instead of
    hand-typing the same string three times; pass either explicitly to
    override (e.g. no env channel, or a config key that differs from name).
    """
    resolved_env: str | None = (
        f"REPO_OPS_{name.upper()}" if isinstance(env_var, _Derive) else env_var
    )
    resolved_key: str = name if isinstance(config_key, _Derive) else config_key
    return _Setting(name, resolved_env, resolved_key, default, flag, default_doc)


# Single source of truth for settings resolution and documentation — env_var,
# config_key, default (used by resolve_setting) and flag, default_doc (used
# by SETTINGS_TABLE) all live on one row per setting so they can't drift.
# Timeout defaults match repo_ops_core.constants (single source of truth).
_SETTINGS: tuple[_Setting, ...] = (
    # Execution
    _s("jobs", None, "--jobs", "min(16, cpu_count)", env_var=None),
    # Upgrade policy
    _s("go_upgrade_mode", None, "--go-upgrade-mode", "patch"),
    _s("actions_lint_level", None, "--actions-lint-level", "block"),
    # Timeouts (defaults from constants — same values resolve_timeout_* use)
    _s("timeout_check", TIMEOUT_CHECK, "(none)", str(TIMEOUT_CHECK)),
    _s("timeout_apply", TIMEOUT_APPLY, "(none)", str(TIMEOUT_APPLY)),
    _s("timeout_verify", TIMEOUT_VERIFY, "(none)", str(TIMEOUT_VERIFY)),
    _s("find_timeout", FIND_TIMEOUT, "(none)", str(FIND_TIMEOUT)),
    _s("git_timeout", GIT_TIMEOUT, "(none)", str(GIT_TIMEOUT)),
    # Feature flags
    # Sweep-default settings: these 20 keys back off/on the default full
    # sweep and default to "1" (on). --conservative is the only way to flip
    # them all to "0" at once; each is still individually overridable via its
    # own env var or [tool.repo-ops] config key, which is the entire point of
    # routing them through this table instead of an unconditional env write.
    _s("bun_update_latest", "1", "--conservative", "1"),
    _s("pnpm_update_latest", "1", "--conservative", "1"),
    _s("yarn_update_latest", "1", "--conservative", "1"),
    _s("yarn_targeted_bump", "1", "--conservative", "1"),
    _s("yarn_max_concurrency", None, "(none)", "4"),
    _s("npm_targeted_bump", "1", "--conservative", "1"),
    _s("pnpm_targeted_bump", "1", "--conservative", "1"),
    _s("bun_targeted_bump", "1", "--conservative", "1"),
    _s("deno_update_latest", "1", "--conservative", "1"),
    _s("deno_targeted_bump", "1", "--conservative", "1"),
    _s("rust_targeted_bump", "1", "--conservative", "1"),
    _s("poetry_targeted_bump", "1", "--conservative", "1"),
    _s("pdm_targeted_bump", "1", "--conservative", "1"),
    _s("pipenv_targeted_bump", "1", "--conservative", "1"),
    _s("pip_tools_targeted_bump", "1", "--conservative", "1"),
    _s("go_work_sync", None, "(none)", "0"),
    _s("no_progress", None, "(none)", "0"),
    _s("no_state", None, "(none)", "0"),
    _s("no_lint_cache", None, "(none)", "0"),
    _s("go_directive", None, "(none)", "(none)"),
    _s("go_toolchain", None, "(none)", "(none)"),
    _s("node_verify_script", None, "(none)", "(none)"),
    _s("commit_agent_provider", None, "(none)", "cursor"),
    _s("commit_agent_model", "composer-2.5", "(none)", "composer-2.5"),
    _s("commit_umbrella", None, "--commit-umbrella", "0"),
    _s("actions_org_policy", None, "--actions-org-policy", "0"),
    _s("actions_policy_level", None, "--actions-policy-level", "report"),
    # Actions digest refresh (SHA-pinned Actions get their pin's commit
    # advanced within the resolved major) and pin-to-SHA (a floating-tag
    # Action gets hardened to a SHA pin). Both are opt-in-by-sweep-default,
    # AND-ed with keep_sha (never fires when --actions-unpin-sha is passed).
    _s("actions_refresh_sha", "1", "--conservative", "1"),
    _s("actions_pin_sha", "1", "--conservative", "1"),
    _s("container_update_latest", "1", "--conservative", "1"),
    # Apply resume checkpoint (apply-only): skip repos already applied in a prior run.
    _s("resume", None, "(none)", "0"),
    # Submodule tag-advance: allow cross-major semver tag bumps on submodule pointers.
    _s("submodule_update_latest", "1", "--conservative", "1"),
    # Pre-commit hook upgrades: allow cross-major rev bumps.
    _s("precommit_update_latest", "1", "--conservative", "1"),
    _s("precommit_refresh_sha", "1", "--conservative", "1"),
    _s("submodule_refresh_sha", "1", "--conservative", "1"),
    # Container ecosystem: refresh image digest for SHA-pinned references.
    _s("container_refresh_sha", "1", "--conservative", "1"),
    # Hatch: opt in to creating uv.lock (also settable via --migrate-to-uv-lock flag).
    _s("hatch_migrate_uv_lock", None, "--migrate-to-uv-lock", "1"),
    # Exclude patterns: CLI --exclude / config key ``exclude`` (list). No env
    # channel — lists do not serialize cleanly; run.py also special-cases merge.
    _s("exclude_patterns", None, "--exclude", "[]", env_var=None, config_key="exclude"),
    # Audit fail-on gates: readable from [tool.repo-ops] so CI can pin the
    # severity threshold without repeating --fail-on / --fail-on-unknown on
    # every invocation. Consumed by repo_ops_audit's main() (CLI flag > env >
    # config > default); repo_ops_core only owns the settings-table rows.
    _s("audit_fail_on", None, "--fail-on", "high"),
    _s("audit_fail_on_unknown", None, "--fail-on-unknown", "0"),
)

# Maps setting name → (env_var, config_key, default); consumed directly by
# resolve_setting and by callers that only need the resolution triple.
_SETTINGS_MAP: dict[str, tuple[str | None, str, Any]] = {
    s.name: (s.env_var, s.config_key, s.default) for s in _SETTINGS
}

# Public table for documentation generation: (setting, flag, env_var, config_key, default).
SETTINGS_TABLE: list[tuple[str, str, str, str, str]] = [
    (s.name, s.flag, s.env_var or "(none)", s.config_key, s.default_doc) for s in _SETTINGS
]


_ambient_env: Mapping[str, str] | None = None
_checkout_config: ContextVar[tuple[dict[str, Any], dict[str, Any]] | None] = ContextVar(
    "repo_ops_checkout_config", default=None
)


def set_ambient_env(env: Mapping[str, str] | None) -> None:
    """Set the caller-provided environment snapshot for config resolution."""
    global _ambient_env
    _ambient_env = dict(env) if env is not None else None


def ambient_env() -> Mapping[str, str] | None:
    """Return the caller-provided environment snapshot, when configured."""
    return _ambient_env


def process_env_cli_override(env_var: str) -> str | None:
    """Return a process value that differs from the ambient environment."""
    ambient = ambient_env()
    if ambient is None:
        return None
    process_value = os.environ.get(env_var)
    if env_var not in ambient:
        return process_value
    return process_value if process_value != ambient[env_var] else None


@contextmanager
def checkout_config_scope(
    *,
    global_cfg: Mapping[str, Any] | None = None,
    repo_cfg: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    """Resolve config-file settings for the checkout active in this context."""
    token = _checkout_config.set((dict(global_cfg or {}), dict(repo_cfg or {})))
    try:
        yield
    finally:
        _checkout_config.reset(token)


def active_checkout_config() -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return config layers for the checkout active in this context."""
    return _checkout_config.get()


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
    env: Mapping[str, str] | None = None,
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
    env:
        Optional environment snapshot used for resolution.  Passing a snapshot
        lets callers distinguish caller-provided environment values from
        config values injected into the process environment for compatibility.
    """
    entry = _SETTINGS_MAP.get(name)
    if entry is None:
        raise KeyError(f"unknown setting: {name!r}")
    env_var, config_key, default = entry

    if cli_value is not None:
        return cli_value
    if env_var:
        env_val = (os.environ if env is None else env).get(env_var)
        if env_val is not None:
            return env_val
    if repo_cfg and config_key in repo_cfg:
        return repo_cfg[config_key]
    if global_cfg and config_key in global_cfg:
        return global_cfg[config_key]
    return default


_BOOL_TRUE_VALUES = ("1", "true", "yes", "on")


def coerce_bool_setting(raw: Any) -> bool:
    """Normalize a resolved setting value (bool, ``"1"``/``"true"``/etc, or None) to bool."""
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _BOOL_TRUE_VALUES


def resolve_bool_setting(name: str, env_var: str, ws: Any = None) -> bool:
    """Resolve a boolean feature-flag setting: CLI > env > config > default.

    ``ws`` supplies the per-repo config scope — any object with a
    ``git_repo`` path attribute (e.g. ``repo_ops_models.Workspace``); omit
    to resolve from global config only.

    Callers that need ``ambient_env``/``load_repo_config``/``load_global_config``
    individually patchable (e.g. a test suite mocking the config layer at the
    call site's module) should call :func:`resolve_setting` directly and pass
    the result through :func:`coerce_bool_setting` instead.
    """
    raw = resolve_setting(
        name,
        cli_value=process_env_cli_override(env_var),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo) if ws is not None else None,
        global_cfg=load_global_config(),
    )
    return coerce_bool_setting(raw)
