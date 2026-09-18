"""repo-ops config: two TOML files, three keys (read-only — never written).

  Global:   $XDG_CONFIG_HOME/repo-ops/config.toml (default ~/.config/repo-ops/config.toml)
  Per-repo: <repo_root>/.repo-ops.toml

Keys:
  exclude     list of repo patterns; unioned across both files and --exclude
  [[hold]]    package version ceilings (see repo_ops_core.holds)
  [verify]    per-repo verify profile (see repo_ops_upgrade.verify)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from repo_ops_core.filesystem import read_toml_file


def load_global_config() -> dict[str, Any]:
    """Load ``$XDG_CONFIG_HOME/repo-ops/config.toml`` (``{}`` when absent)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return read_toml_file(base / "repo-ops" / "config.toml") or {}


def load_repo_config(repo_root: Path) -> dict[str, Any]:
    """Load ``<repo_root>/.repo-ops.toml`` (``{}`` when absent)."""
    return read_toml_file(repo_root / ".repo-ops.toml") or {}
