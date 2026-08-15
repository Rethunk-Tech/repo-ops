"""Commit-message drafting for ``--commit=agent`` (Cursor SDK optional)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from repo_ops_core.config import (
    ambient_env as _config_ambient_env,
)
from repo_ops_core.config import (
    load_global_config,
    load_repo_config,
    resolve_setting,
)
from repo_ops_core.config import (
    set_ambient_env as _config_set_ambient_env,
)
from repo_ops_models import RepoBundle

COMMIT_AGENT_PROVIDER_ENV = "REPO_OPS_COMMIT_AGENT_PROVIDER"
DEFAULT_COMMIT_AGENT_PROVIDER = "cursor"

# Canonical extra; ``agent`` remains a backward-compatible alias in pyproject.toml.
COMMIT_AGENT_CURSOR_EXTRA = "commit-agent-cursor"
COMMIT_AGENT_INSTALL_HINT = (
    f"uv pip install 'repo-ops-upgrade[{COMMIT_AGENT_CURSOR_EXTRA}]' "
    f"(or repo-ops-upgrade[agent])"
)


class CommitAgentProvider(Protocol):
    """Draft a full commit message (subject + optional body) for post-apply commits."""

    @property
    def name(self) -> str: ...

    def draft_commit_message(self, bundle: RepoBundle, files: list[str]) -> str: ...


def set_ambient_env(env: Mapping[str, str] | None) -> None:
    """Set the caller-provided environment snapshot for config resolution."""
    _config_set_ambient_env(env)


def ambient_env() -> Mapping[str, str] | None:
    """Return the caller-provided environment snapshot, when configured."""
    return _config_ambient_env()


def known_commit_agent_providers() -> list[str]:
    return [DEFAULT_COMMIT_AGENT_PROVIDER]


def commit_agent_provider_name(repo_path: Path | None = None) -> str:
    """Resolve the provider id from the settings layer."""
    raw_provider = resolve_setting(
        "commit_agent_provider",
        global_cfg=load_global_config(),
        repo_cfg=load_repo_config(repo_path) if repo_path is not None else None,
        env=ambient_env(),
    )
    if raw_provider is None:
        return DEFAULT_COMMIT_AGENT_PROVIDER
    if not isinstance(raw_provider, str):
        raise RuntimeError(
            f"{COMMIT_AGENT_PROVIDER_ENV} must be a non-empty provider identifier string"
        )
    provider = raw_provider.strip().lower()
    if not provider:
        raise RuntimeError(
            f"{COMMIT_AGENT_PROVIDER_ENV} must be a non-empty provider identifier"
        )
    return provider


def resolve_commit_agent_provider(
    name: str | None = None,
    *,
    repo_path: Path | None = None,
) -> CommitAgentProvider:
    """Instantiate the configured commit-agent provider."""
    key = (name or commit_agent_provider_name(repo_path)).strip().lower()
    if key != DEFAULT_COMMIT_AGENT_PROVIDER:
        known = ", ".join(known_commit_agent_providers())
        raise RuntimeError(
            f"Unknown commit agent provider {key!r} "
            f"(known: {known}). "
            f"Set {COMMIT_AGENT_PROVIDER_ENV} or use --commit=conventional."
        )
    from repo_ops_upgrade.commit_agent_cursor import CursorCommitAgentProvider

    return CursorCommitAgentProvider()


def draft_message_with_commit_agent(
    bundle: RepoBundle,
    files: list[str],
    *,
    provider_name: str | None = None,
) -> str:
    """Draft a commit message via the selected commit-agent provider."""
    provider = resolve_commit_agent_provider(
        provider_name,
        repo_path=bundle.git_repo,
    )
    return provider.draft_commit_message(bundle, files)
