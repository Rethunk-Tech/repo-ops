"""Cursor SDK commit-agent provider (lazy ``cursor_sdk`` import)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
    resolve_setting,
)
from repo_ops_models import RepoBundle
from repo_ops_upgrade.commit_agent_providers import COMMIT_AGENT_INSTALL_HINT


def _resolve_agent_model(repo_path: Path | None = None) -> str:
    raw_model = resolve_setting(
        "commit_agent_model",
        global_cfg=load_global_config(),
        repo_cfg=load_repo_config(repo_path) if repo_path is not None else None,
        env=ambient_env(),
    )
    if not isinstance(raw_model, str):
        raise RuntimeError(
            "REPO_OPS_COMMIT_AGENT_MODEL must be a non-empty model identifier string"
        )
    model = raw_model.strip()
    if not model:
        raise RuntimeError("REPO_OPS_COMMIT_AGENT_MODEL must be a non-empty model identifier")
    return model


def _normalize_agent_subject(text: str) -> str:
    line = ""
    for raw in text.splitlines():
        candidate = raw.strip().strip("`\"'")
        if candidate:
            line = candidate
            break
    if not line:
        return "chore(deps): apply repo-ops dependency updates"
    line = re.sub(r"\s+", " ", line)
    if len(line) > 72:
        line = line[:69].rstrip() + "..."
    return line


class CursorCommitAgentProvider:
    """Draft commit subjects via Cursor SDK ``Agent.prompt``."""

    name = "cursor"

    def draft_commit_message(
        self,
        bundle: RepoBundle,
        files: list[str],
        *,
        api_key: str | None = None,
    ) -> str:
        key = (api_key or os.environ.get("CURSOR_API_KEY") or "").strip()
        if not key:
            raise RuntimeError(
                "CURSOR_API_KEY is not set; export a Cursor API key or use "
                f"--commit=conventional. Install optional agent support: "
                f"{COMMIT_AGENT_INSTALL_HINT}"
            )
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        except ImportError as exc:
            raise RuntimeError(
                "cursor-sdk is not installed for the cursor commit-agent provider; "
                f"{COMMIT_AGENT_INSTALL_HINT}"
            ) from exc

        file_blob = "\n".join(f"- {p}" for p in files[:40])
        if len(files) > 40:
            file_blob += f"\n- … and {len(files) - 40} more"
        prompt = (
            "Draft exactly one Conventional Commits subject line (max 72 characters) "
            "for a dependency or CI maintenance commit in this repository.\n"
            f"Repository label: {bundle.label}\n"
            f"Changed files:\n{file_blob}\n\n"
            "Return only the subject line, no quotes or body."
        )
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=key,
                model=_resolve_agent_model(bundle.git_repo),
                local=LocalAgentOptions(cwd=str(bundle.git_repo.resolve())),
            ),
        )
        if getattr(result, "status", None) == "error":
            rid = getattr(result, "id", "?")
            raise RuntimeError(f"Cursor agent run failed (run id {rid})")
        text = getattr(result, "result", None) or getattr(result, "text", None) or ""
        if not isinstance(text, str):
            text = str(text)
        subject = _normalize_agent_subject(text)
        return f"{subject}\n\nAutomated post-apply commit (cursor commit-agent)."
