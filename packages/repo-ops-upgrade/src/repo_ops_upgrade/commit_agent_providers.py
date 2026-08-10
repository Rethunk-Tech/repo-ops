"""Pluggable commit-message agents for ``--commit=agent`` (Cursor SDK optional)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from repo_ops_core.config import load_global_config, load_repo_config, resolve_setting
from repo_ops_models import RepoBundle

COMMIT_AGENT_PROVIDER_ENV = "REPO_OPS_COMMIT_AGENT_PROVIDER"
DEFAULT_COMMIT_AGENT_PROVIDER = "cursor"

# Canonical extra; ``agent`` remains a backward-compatible alias in pyproject.toml.
COMMIT_AGENT_CURSOR_EXTRA = "commit-agent-cursor"
COMMIT_AGENT_INSTALL_HINT = (
    f"uv pip install 'repo-ops-upgrade[{COMMIT_AGENT_CURSOR_EXTRA}]' "
    f"(or repo-ops-upgrade[agent])"
)


@runtime_checkable
class CommitAgentProvider(Protocol):
    """Draft a full commit message (subject + optional body) for post-apply commits."""

    @property
    def name(self) -> str: ...

    def draft_commit_message(self, bundle: RepoBundle, files: list[str]) -> str: ...


_ProviderFactory = Callable[[], CommitAgentProvider]

_FACTORIES: dict[str, _ProviderFactory] = {}
_builtins_registered = False


def register_commit_agent_provider(name: str, factory: _ProviderFactory) -> None:
    """Register a provider factory (for built-ins or third-party extensions)."""
    _FACTORIES[name] = factory


def known_commit_agent_providers() -> list[str]:
    return sorted(_FACTORIES)


def commit_agent_provider_name(repo_path: Path | None = None) -> str:
    """Resolve the provider id from the settings layer."""
    raw_provider = resolve_setting(
        "commit_agent_provider",
        global_cfg=load_global_config(),
        repo_cfg=load_repo_config(repo_path) if repo_path is not None else None,
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


def _ensure_builtin_providers() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    from repo_ops_upgrade.commit_agent_cursor import CursorCommitAgentProvider

    register_commit_agent_provider(
        CursorCommitAgentProvider.name,
        CursorCommitAgentProvider,
    )
    _builtins_registered = True


def resolve_commit_agent_provider(
    name: str | None = None,
    *,
    repo_path: Path | None = None,
) -> CommitAgentProvider:
    """Instantiate the configured commit-agent provider."""
    _ensure_builtin_providers()
    key = (name or commit_agent_provider_name(repo_path)).strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        known = ", ".join(known_commit_agent_providers()) or "(none)"
        raise RuntimeError(
            f"Unknown commit agent provider {key!r} "
            f"(known: {known}). "
            f"Set {COMMIT_AGENT_PROVIDER_ENV} or use --commit=conventional."
        )
    provider = factory()
    if not isinstance(provider, CommitAgentProvider):
        raise RuntimeError(
            f"Provider {key!r} factory did not return a CommitAgentProvider"
        )
    return provider


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
