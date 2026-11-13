"""Biome bump: safe fixes are owned edits; a bump that still fails verify is held, not dropped."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_js.holds import resolve_package_hold
from repo_ops_models import Change, Workspace, WorkspaceResult
from repo_ops_upgrade import orchestration
from repo_ops_upgrade_remediations_js import remediations

BIOME = "@biomejs/biome"


def _workspace(tmp_path: Path, init_git_repo) -> Workspace:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text('{"name":"r","packageManager":"npm@10.0.0"}')
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":1,"packages":{}}')
    (tmp_path / "biome.json").write_text(
        '{"$schema":"https://biomejs.dev/schemas/2.0.0/schema.json"}'
    )
    (tmp_path / "a.ts").write_text("let a = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label="d")


def test_apply_biome_runs_safe_check_write_never_unsafe(tmp_path: Path, make_proc) -> None:
    ws = Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label="d")
    (tmp_path / "biome.json").write_text("{}")
    calls: list[list[str]] = []

    def fake_run(cmd, *_a, **_k):
        calls.append(list(cmd))
        # check exits 1 while diagnostics remain; that is verify's call, not a failed remediation
        return make_proc(returncode=1 if "check" in cmd else 0)

    with patch.object(remediations, "run", fake_run):
        ok, detail = remediations.apply_biome(ws, {})

    assert ok, detail
    assert [c for c in calls if "migrate" in c] and [c for c in calls if "check" in c]
    check = next(c for c in calls if "check" in c)
    assert "--write" in check and not any("unsafe" in a for a in check)


def test_failed_verify_holds_biome_and_lands_the_rest(tmp_path: Path, init_git_repo) -> None:
    ws = _workspace(tmp_path, init_git_repo)
    lock = tmp_path / "package-lock.json"
    original_lock = lock.read_bytes()
    register_all()
    seen: list[object] = []
    verdicts = iter(["biome lint still red", None])

    def apply(workspace: Workspace) -> None:
        seen.append(resolve_package_hold(workspace, BIOME))
        # a held run must start from the reverted tree
        assert lock.read_bytes() == original_lock
        assert (tmp_path / "a.ts").read_text() == "let a = 1\n"
        lock.write_text(f'{{"lockfileVersion":2,"n":{len(seen)}}}')

    def remediate(result: WorkspaceResult, *, apply: bool) -> None:
        if seen[-1] is None:  # first pass: the safe fix edits a source file
            (tmp_path / "a.ts").write_text("const a = 1;\n")

    def diff(_before, _after, _manager):
        if seen[-1] is None:
            return [Change(BIOME, "2.0.0", "2.4.0")]
        return [Change("left-pad", "1.0.0", "1.3.0")]

    with (
        patch("repo_ops_upgrade.orchestration.ecosystems.apply_workspace", side_effect=apply),
        patch("repo_ops_upgrade.orchestration.diff_workspace_snapshots", side_effect=diff),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations", side_effect=remediate),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            side_effect=lambda *_a, **_k: next(verdicts),
        ),
    ):
        out = orchestration.process_workspace(
            WorkspaceResult(workspace=ws),
            do_apply=True,
            do_verify=True,
            allow_dirty=False,
            on_verify_fail="revert",
        )

    assert seen[0] is None and seen[1] is not None and seen[1].max_version == "2.0.0"
    assert out.status == "ok"
    assert [c.name for c in out.changes] == ["left-pad"]
    held = [r for r in out.remediations if r["id"] == "biome_hold"]
    assert held and "2.0.0" in held[0]["detail"] and "biome lint still red" in held[0]["detail"]
    assert resolve_package_hold(ws, BIOME) is None  # the hold is per-run only
