"""Coverage for CursorCommitAgentProvider error/edge branches (32% -> covered).

Exercises: missing CURSOR_API_KEY (RuntimeError), cursor_sdk not installed
(ImportError -> RuntimeError), Agent.prompt returning an error status
(RuntimeError), the happy path (including >40 files truncation and the
draft_message_with_agent wrapper), and _normalize_agent_subject's line
selection / quote-stripping / whitespace-collapsing / truncation logic.
No real Cursor API key or cursor-sdk install required.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from repo_ops_models import RepoBundle
from repo_ops_upgrade import commit_agent_providers
from repo_ops_upgrade.commit_agent_cursor import (
    CursorCommitAgentProvider,
    _normalize_agent_subject,
    draft_message_with_agent,
)


def _bundle(tmp_path: Path) -> RepoBundle:
    return RepoBundle(git_repo=tmp_path, label="demo-repo")


def _isolate_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def test_missing_api_key_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    provider = CursorCommitAgentProvider()
    with pytest.raises(RuntimeError, match="CURSOR_API_KEY"):
        provider.draft_commit_message(_bundle(tmp_path), ["a.txt"], api_key=None)


def test_cursor_sdk_not_installed_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # sys.modules[name] = None is the standard trick to force ImportError on
    # `import name`/`from name import ...` regardless of whether the real
    # package happens to be installed in this environment.
    monkeypatch.setitem(sys.modules, "cursor_sdk", None)
    provider = CursorCommitAgentProvider()
    with pytest.raises(RuntimeError, match="cursor-sdk is not installed"):
        provider.draft_commit_message(_bundle(tmp_path), ["a.txt"], api_key="test-key")


def _install_fake_cursor_sdk(
    monkeypatch: pytest.MonkeyPatch, result_obj: object, captured: dict
) -> None:
    fake_mod = types.ModuleType("cursor_sdk")

    class Agent:
        @staticmethod
        def prompt(prompt: str, options: object) -> object:
            captured["prompt"] = prompt
            captured["options"] = options
            return result_obj

    class AgentOptions:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    class LocalAgentOptions:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    fake_mod.Agent = Agent  # type: ignore[attr-defined]
    fake_mod.AgentOptions = AgentOptions  # type: ignore[attr-defined]
    fake_mod.LocalAgentOptions = LocalAgentOptions  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_mod)


def test_draft_commit_message_success_via_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_config_home(monkeypatch, tmp_path)
    monkeypatch.delenv("REPO_OPS_COMMIT_AGENT_MODEL", raising=False)
    result = types.SimpleNamespace(status="ok", result="`feat(x): bump deps`\n")
    captured: dict = {}
    _install_fake_cursor_sdk(monkeypatch, result, captured)

    files = [f"pkg{i}/file.txt" for i in range(45)]  # exercise >40 truncation
    message = draft_message_with_agent(_bundle(tmp_path), files, api_key="test-key")

    assert message.startswith("feat(x): bump deps\n\n")
    assert "Automated post-apply commit (cursor commit-agent)." in message
    assert "and 5 more" in captured["prompt"]
    assert captured["options"].api_key == "test-key"
    assert captured["options"].model == "composer-2.5"


def test_draft_commit_message_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_config_home(monkeypatch, tmp_path)
    monkeypatch.setenv("REPO_OPS_COMMIT_AGENT_MODEL", "gpt-5.6-luna-xhigh")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.repo-ops]\ncommit_agent_model = 'repo-model'\n", encoding="utf-8"
    )
    result = types.SimpleNamespace(status="ok", result="fix: update deps")
    captured: dict = {}
    _install_fake_cursor_sdk(monkeypatch, result, captured)

    CursorCommitAgentProvider().draft_commit_message(
        _bundle(tmp_path), ["a.txt"], api_key="test-key"
    )

    assert captured["options"].model == "gpt-5.6-luna-xhigh"


def test_draft_commit_message_uses_target_repo_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_config_home(monkeypatch, tmp_path)
    monkeypatch.delenv("REPO_OPS_COMMIT_AGENT_MODEL", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.repo-ops]\ncommit_agent_model = 'repo-model'\n", encoding="utf-8"
    )
    result = types.SimpleNamespace(status="ok", result="fix: update deps")
    captured: dict = {}
    _install_fake_cursor_sdk(monkeypatch, result, captured)

    CursorCommitAgentProvider().draft_commit_message(
        _bundle(tmp_path), ["a.txt"], api_key="test-key"
    )

    assert captured["options"].model == "repo-model"


def test_draft_commit_message_uses_target_repo_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_config_home(monkeypatch, tmp_path)
    monkeypatch.delenv(commit_agent_providers.COMMIT_AGENT_PROVIDER_ENV, raising=False)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.repo-ops]\ncommit_agent_provider = 'repo-provider'\n", encoding="utf-8"
    )

    class _StubProvider:
        name = "repo-provider"

        def draft_commit_message(self, bundle: RepoBundle, files: list[str]) -> str:
            return "chore(deps): repo provider"

    commit_agent_providers.register_commit_agent_provider(
        "repo-provider", _StubProvider
    )
    try:
        message = commit_agent_providers.draft_message_with_commit_agent(
            _bundle(tmp_path), ["a.txt"]
        )
    finally:
        commit_agent_providers._FACTORIES.pop("repo-provider", None)

    assert message == "chore(deps): repo provider"


def test_draft_commit_message_rejects_empty_configured_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_config_home(monkeypatch, tmp_path)
    monkeypatch.setenv("REPO_OPS_COMMIT_AGENT_MODEL", "  ")
    result = types.SimpleNamespace(status="ok", result="fix: update deps")
    captured: dict = {}
    _install_fake_cursor_sdk(monkeypatch, result, captured)

    with pytest.raises(
        RuntimeError, match="REPO_OPS_COMMIT_AGENT_MODEL must be a non-empty"
    ):
        CursorCommitAgentProvider().draft_commit_message(
            _bundle(tmp_path), ["a.txt"], api_key="test-key"
        )


def test_draft_commit_message_non_string_result_is_stringified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """result.result may be a non-str object (e.g. SDK returns a rich type)."""
    _isolate_config_home(monkeypatch, tmp_path)
    result = types.SimpleNamespace(status="ok", result=None, text=42)
    captured: dict = {}
    _install_fake_cursor_sdk(monkeypatch, result, captured)

    provider = CursorCommitAgentProvider()
    message = provider.draft_commit_message(_bundle(tmp_path), ["a.txt"], api_key="k")
    assert message.startswith("42\n\n")


def test_draft_commit_message_error_status_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_config_home(monkeypatch, tmp_path)
    result = types.SimpleNamespace(status="error", id="run-123")
    captured: dict = {}
    _install_fake_cursor_sdk(monkeypatch, result, captured)

    provider = CursorCommitAgentProvider()
    with pytest.raises(RuntimeError, match="run-123"):
        provider.draft_commit_message(_bundle(tmp_path), ["a.txt"], api_key="k")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "chore(deps): apply repo-ops dependency updates"),
        ("   \n  \n", "chore(deps): apply repo-ops dependency updates"),
        ("`feat(x): bump deps`", "feat(x): bump deps"),
        ("\n\n  fix(y): thing  \nbody line", "fix(y): thing"),
        ("word1   word2\tword3", "word1 word2 word3"),
        ("x" * 100, "x" * 69 + "..."),
    ],
)
def test_normalize_agent_subject_variants(text: str, expected: str) -> None:
    assert _normalize_agent_subject(text) == expected


def test_normalize_agent_subject_truncated_length_is_72() -> None:
    subject = _normalize_agent_subject("y" * 200)
    assert len(subject) == 72
    assert subject.endswith("...")
