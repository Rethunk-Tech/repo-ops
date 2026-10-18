"""Shared defaults for repo-ops commands."""

from __future__ import annotations

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
    str(Path.home() / "Apps"),
]

# Subprocess timeouts, in seconds.
TIMEOUT_CHECK = 60
TIMEOUT_APPLY = 600
TIMEOUT_VERIFY = 60

# An enclosing go.work (a superproject over submodule checkouts) makes MVS select
# the newest version any member requires, hiding updates the module's own go.mod
# still needs and building against sibling checkouts instead of that go.mod.
GO_MODULE_MODE_ENV = {"GOWORK": "off"}
FIND_TIMEOUT = 60
GIT_TIMEOUT = 10
