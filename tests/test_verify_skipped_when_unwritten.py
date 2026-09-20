"""Verify runs for an upgrade this run made, and not for one it did not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repo_ops_models import WorkspaceResult
from repo_ops_upgrade import orchestration


def _run(monkeypatch: Any, ws: Any, *, writes: str | None) -> tuple[WorkspaceResult, list[str]]:
    verified: list[str] = []

    def _apply(workspace: Any) -> list:
        if writes is not None:
            (Path(workspace.path) / "package.json").write_text(writes, encoding="utf-8")
        return []

    monkeypatch.setattr(orchestration.ecosystems, "apply_workspace", _apply)
    monkeypatch.setattr(orchestration, "dispatch_remediations", lambda *a, **k: None)
    monkeypatch.setattr(
        orchestration.verify_mod,
        "verify_workspace",
        lambda w: verified.append(str(w.path)) or None,
    )
    result = orchestration._process_workspace_body(
        WorkspaceResult(workspace=ws),
        do_apply=True,
        do_verify=True,
        allow_dirty=False,
        on_verify_fail="report",
    )
    return result, verified


def test_verify_is_skipped_when_the_apply_wrote_nothing(
    tmp_path, monkeypatch, init_git_repo, make_workspace
):
    init_git_repo(tmp_path, commit=True, filename="package.json", content="{}\n")
    ws = make_workspace(tmp_path)
    result, verified = _run(monkeypatch, ws, writes=None)
    assert verified == []
    assert result.verify_skipped is True


def test_verify_runs_when_the_apply_wrote_a_file(
    tmp_path, monkeypatch, init_git_repo, make_workspace
):
    init_git_repo(tmp_path, commit=True, filename="package.json", content="{}\n")
    ws = make_workspace(tmp_path)
    result, verified = _run(monkeypatch, ws, writes='{"name":"changed"}\n')
    assert verified == [str(tmp_path)]
    assert result.verify_skipped is False
