"""Shared defaults for repo-ops commands."""

from __future__ import annotations

import shutil
import subprocess
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

_FALLBACK_ROOTS = [
    "/usr/local/src",
    str(Path.home() / "Projects"),
    str(Path.home() / "Documents"),
    str(Path.home() / "Apps"),
]


def default_roots() -> list[str]:
    """The lines of `bosun roots` when bosun is installed and prints any, else the built-in list."""
    exe = shutil.which("bosun")
    if exe:
        try:
            out = subprocess.run(
                [exe, "roots"], capture_output=True, text=True, timeout=10, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return list(_FALLBACK_ROOTS)
        roots = [line.strip() for line in out.splitlines() if line.strip()]
        if roots:
            return roots
    return list(_FALLBACK_ROOTS)


# Subprocess timeouts, in seconds.
TIMEOUT_CHECK = 60
# Asking the proxy for every module's newest version takes over a minute on a large module graph.
TIMEOUT_LIST_UPDATES = 300
TIMEOUT_APPLY = 600
# A gate that runs a real test suite is slow; this is a ceiling for a hang, not
# a budget. Verify runs the same full gate, so it shares the ceiling.
TIMEOUT_GATE = 1800
TIMEOUT_VERIFY = TIMEOUT_GATE

# An enclosing go.work (a superproject over submodule checkouts) makes MVS select
# the newest version any member requires, hiding updates the module's own go.mod
# still needs and building against sibling checkouts instead of that go.mod.
GO_MODULE_MODE_ENV = {"GOWORK": "off"}
FIND_TIMEOUT = 60
GIT_TIMEOUT = 10
