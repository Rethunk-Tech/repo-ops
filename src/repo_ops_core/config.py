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
    # Timeouts (defaults from constants — same values resolve_timeout_* use)
    _s("timeout_check", TIMEOUT_CHECK, "(none)", str(TIMEOUT_CHECK)),
    _s("timeout_apply", TIMEOUT_APPLY, "(none)", str(TIMEOUT_APPLY)),
    _s("timeout_verify", TIMEOUT_VERIFY, "(none)", str(TIMEOUT_VERIFY)),
    _s("find_timeout", FIND_TIMEOUT, "(none)", str(FIND_TIMEOUT)),
    _s("git_timeout", GIT_TIMEOUT, "(none)", str(GIT_TIMEOUT)),
    # Feature flags
    _s("yarn_max_concurrency", None, "(none)", "4"),
    _s("go_work_sync", None, "(none)", "0"),
    _s("no_progress", None, "(none)", "0"),
    _s("no_state", None, "(none)", "0"),
    _s("no_lint_cache", None, "(none)", "0"),
    _s("go_directive", None, "(none)", "(none)"),
    _s("go_toolchain", None, "(none)", "(none)"),
    _s("node_verify_script", None, "(none)", "(none)"),
    # Exclude patterns: CLI --exclude / config key ``exclude`` (list). No env
    # channel — lists do not serialize cleanly; run.py also special-cases merge.
    _s("exclude_patterns", None, "--exclude", "[]", env_var=None, config_key="exclude"),
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
