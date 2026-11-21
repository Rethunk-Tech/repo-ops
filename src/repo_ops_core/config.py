"""repo-ops config: two TOML files, six keys (read-only — never written).

  Global:   $XDG_CONFIG_HOME/repo-ops/config.toml (default ~/.config/repo-ops/config.toml)
  Per-repo: <repo_root>/.repo-ops.toml

Keys:
  owned_orgs  global only: orgs whose checkouts may be applied to (repo_ops_core.owned)
  adopted     global only: "org/repo" forks (an upstream remote) swept like our own repos
  exclude     list of repo patterns; unioned across both files and --exclude
  [[hold]]    package version ceilings (see repo_ops_core.holds)
  [verify]    per-repo verify profile (see repo_ops_upgrade.verify)
  [soup]      repo-relative SOUP table whose name/version rows follow each bump
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """A config file exists but cannot be parsed; *detail* is the parse message."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"unreadable {path}: {detail}")
        self.detail = detail


_KEYS = {
    "": {"owned_orgs", "adopted", "exclude", "hold", "verify", "soup"},
    "verify": {"script", "skip", "timeout"},
    "soup": {"path"},
    "hold": {"package", "max_version", "until", "ecosystem", "workspace", "reason"},
}
_LIST_KEYS = ("owned_orgs", "adopted", "exclude")


def _validate(path: Path, data: dict[str, Any]) -> None:
    """Reject a key repo-ops does not read, or a list key of the wrong type."""
    unknown = sorted(set(data) - _KEYS[""])
    if unknown:
        raise ConfigError(path, f"unknown key(s): {', '.join(unknown)}")
    for key in _LIST_KEYS:
        if key in data and not isinstance(data[key], list):
            raise ConfigError(path, f"{key} must be a list, got {data[key]!r}")
    for table in ("verify", "soup"):
        value = data.get(table)
        extra = sorted(set(value) - _KEYS[table]) if isinstance(value, dict) else []
        if extra:
            raise ConfigError(path, f"unknown key(s) in [{table}]: {', '.join(extra)}")
    holds = data.get("hold")
    for entry in holds if isinstance(holds, list) else []:
        extra = sorted(set(entry) - _KEYS["hold"]) if isinstance(entry, dict) else []
        if extra:
            raise ConfigError(path, f"unknown key(s) in [[hold]]: {', '.join(extra)}")


def _read_strict(path: Path) -> dict[str, Any]:
    """Parse *path*; ``{}`` when absent, :class:`ConfigError` when unparseable.

    A silent ``{}`` on a typo would drop every hold, exclude and verify profile
    while apply carried on.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ConfigError(path, str(exc)) from exc
    _validate(path, data)
    return data


def load_global_config() -> dict[str, Any]:
    """Load ``$XDG_CONFIG_HOME/repo-ops/config.toml`` (``{}`` when absent)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return _read_strict(base / "repo-ops" / "config.toml")


def load_repo_config(repo_root: Path) -> dict[str, Any]:
    """Load ``<repo_root>/.repo-ops.toml`` (``{}`` when absent)."""
    return _read_strict(repo_root / ".repo-ops.toml")
