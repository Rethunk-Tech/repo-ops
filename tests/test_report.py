"""report digest from report.json (real git repos)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_models import DIRTY_SKIP_MARKER, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.post_apply_commit import content_hash
from repo_ops_upgrade.run import (
    _digest_lines_from_payload,
    _print_dirty_skip_table,
    _retry_paths_from_payload,
    main_apply,
    main_report,
)


def _payload(*workspaces: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "repos": [
            {
                "git_repo": "/repo",
                "label": "repo",
                "workspaces": list(workspaces),
                "actions": [],
            }
        ],
    }


def _ws_json(
    path: str,
    *,
    status: str = "ok",
    error: str | None = None,
    verify_error: str | None = None,
    behind_latest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": path,
        "ecosystem": "node",
        "manager": "npm",
        "status": status,
        "changes": [],
        "behind_latest": behind_latest or [],
        "overrides_audit": [],
        "remediations": [],
        "verify_skipped": False,
    }
    if error is not None:
        row["error"] = error
    if verify_error is not None:
        row["verify_error"] = verify_error
    return row


def _write_run_report(state_home: Path, name: str, payload: dict[str, Any]) -> Path:
    run_dir = state_home / "repo-ops" / "runs" / name
    run_dir.mkdir(parents=True)
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    return report_path


def test_report_prints_first_error_line_per_failed_skipped_held(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any, capsys: Any
) -> None:
    failed = tmp_path / "failed"
    skipped = tmp_path / "skipped"
    held_ws = tmp_path / "held"
    init_git_repo(failed, commit=True)
    init_git_repo(skipped, commit=True)
    init_git_repo(held_ws, commit=True)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    payload = _payload(
        _ws_json(str(failed), status="fail", error="boom line1\nstack line2"),
        _ws_json(str(skipped), status="skip", error=f"{DIRTY_SKIP_MARKER}\nmore"),
        _ws_json(
            str(held_ws),
            behind_latest=[{"name": "lodash", "from": "4.0.0", "to": "5.0.0", "held": "4.17.21"}],
        ),
        _ws_json(str(tmp_path / "ok"), status="ok"),
    )
    _write_run_report(state, "20260101T000000.000000Z-apply", payload)

    assert main_report([]) == 0
    out = capsys.readouterr().out
    assert f"fail {failed}  boom line1" in out
    assert "stack line2" not in out
    assert f"skip {skipped}  {DIRTY_SKIP_MARKER}" in out
    assert "more" not in out.split(DIRTY_SKIP_MARKER)[-1]
    assert f"held {held_ws}  lodash held <= 4.17.21" in out
    assert str(tmp_path / "ok") not in out


def test_report_uses_named_run_not_newest(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any, capsys: Any
) -> None:
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    init_git_repo(older, commit=True)
    init_git_repo(newer, commit=True)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _write_run_report(
        state,
        "20260101T000000.000000Z-apply",
        _payload(_ws_json(str(older), status="fail", error="old fail")),
    )
    _write_run_report(
        state,
        "20260102T000000.000000Z-apply",
        _payload(_ws_json(str(newer), status="fail", error="new fail")),
    )

    assert main_report(["20260101T000000.000000Z-apply"]) == 0
    out = capsys.readouterr().out
    assert "old fail" in out
    assert "new fail" not in out


def test_digest_uses_verify_error_when_workspace_error_missing() -> None:
    payload = _payload(
        _ws_json("/pkg", status="fail", verify_error="tests failed\nrest"),
    )
    assert _digest_lines_from_payload(payload) == ["fail /pkg  tests failed"]


def test_apply_prints_digest_as_final_lines(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any, capsys: Any
) -> None:
    pkg = tmp_path / "pkg"
    init_git_repo(pkg, commit=True)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    def _fake_body(*, report_path: Path, **_: Any) -> int:
        payload = _payload(_ws_json(str(pkg), status="fail", error="first\nsecond"))
        report_path.write_text(json.dumps(payload), encoding="utf-8")
        return 0

    with patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body):
        assert main_apply([str(pkg)]) == 0

    out = capsys.readouterr().out
    digest = f"fail {pkg}  first"
    assert digest in out
    assert "second" not in out
    assert out.rstrip().endswith(digest)


def test_retry_paths_are_failed_and_skipped_not_held(tmp_path: Path, init_git_repo: Any) -> None:
    failed = tmp_path / "failed"
    skipped = tmp_path / "skipped"
    held_ws = tmp_path / "held"
    init_git_repo(failed, commit=True)
    init_git_repo(skipped, commit=True)
    init_git_repo(held_ws, commit=True)
    payload = _payload(
        _ws_json(str(failed), status="fail", error="boom"),
        _ws_json(str(skipped), status="skip", error=DIRTY_SKIP_MARKER),
        _ws_json(
            str(held_ws),
            behind_latest=[{"name": "lodash", "from": "4", "to": "5", "held": "4"}],
        ),
    )
    assert _retry_paths_from_payload(payload) == [str(failed), str(skipped)]


def test_apply_retry_failed_reruns_only_failed_and_skipped_paths(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any
) -> None:
    failed = tmp_path / "failed"
    skipped = tmp_path / "skipped"
    held_ws = tmp_path / "held"
    init_git_repo(failed, commit=True)
    init_git_repo(skipped, commit=True)
    init_git_repo(held_ws, commit=True)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _write_run_report(
        state,
        "20260101T000000.000000Z-apply",
        _payload(
            _ws_json(str(failed), status="fail", error="boom"),
            _ws_json(str(skipped), status="skip", error=DIRTY_SKIP_MARKER),
            _ws_json(
                str(held_ws),
                behind_latest=[{"name": "lodash", "from": "4", "to": "5", "held": "4"}],
            ),
        ),
    )
    captured: dict[str, Any] = {}

    def _fake_body(*, roots: list[str], fleet: bool, args: Any, report_path: Path, **_: Any) -> int:
        captured["roots"] = roots
        captured["fleet"] = fleet
        captured["retry_paths"] = list(args.retry_paths)
        report_path.write_text("{}", encoding="utf-8")
        return 0

    with patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body):
        assert main_apply(["--retry-failed"]) == 0

    got = {Path(p).resolve() for p in captured["roots"]}
    assert got == {failed.resolve(), skipped.resolve()}
    assert captured["fleet"] is True
    assert {Path(p).resolve() for p in captured["retry_paths"]} == got


def test_apply_retry_failed_does_not_treat_checkout_as_run(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any
) -> None:
    checkout = tmp_path / "checkout"
    failed = tmp_path / "failed"
    init_git_repo(checkout, commit=True)
    init_git_repo(failed, commit=True)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _write_run_report(
        state,
        "20260101T000000.000000Z-apply",
        _payload(_ws_json(str(failed), status="fail", error="boom")),
    )
    captured: dict[str, Any] = {}

    def _fake_body(*, roots: list[str], fleet: bool, args: Any, report_path: Path, **_: Any) -> int:
        captured["roots"] = roots
        captured["retry_paths"] = list(args.retry_paths)
        report_path.write_text("{}", encoding="utf-8")
        return 0

    with patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body):
        assert main_apply(["--retry-failed", str(checkout)]) == 0

    got = {Path(p).resolve() for p in captured["roots"]}
    assert got == {failed.resolve()}
    assert {Path(p).resolve() for p in captured["retry_paths"]} == got


def test_dirty_skip_table_path_count_and_rerun(
    tmp_path: Path, init_git_repo: Any, capsys: Any
) -> None:
    repo = tmp_path / "dirty"
    init_git_repo(repo, commit=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    wr = WorkspaceResult(
        workspace=Workspace(
            path=repo, ecosystem="node", manager="npm", git_repo=repo, label="dirty"
        ),
        status="skip",
        error=f"{DIRTY_SKIP_MARKER} (name the checkout as a root to update it anyway)",
    )
    bundle = RepoBundle(git_repo=repo, label="dirty", workspaces=[wr])
    _print_dirty_skip_table({repo: bundle})
    out = capsys.readouterr().out
    assert str(repo) in out
    assert "2" in out
    assert f"repo-ops apply {repo}" in out
    assert "dirty-skipped repos:" in out


def test_commit_failure_is_recorded_digested_and_retried(
    tmp_path: Path, init_git_repo: Any
) -> None:
    from repo_ops_models import RepoBundle
    from repo_ops_upgrade.post_apply_commit import CommitOutcome
    from repo_ops_upgrade.report import build_json_payload

    repo = tmp_path / "r"
    init_git_repo(repo, commit=True)
    bundles = {repo: RepoBundle(git_repo=repo, label="r", workspaces=[])}
    outcomes = [
        CommitOutcome(git_repo=repo, label="r", status="failed", detail="signing failed"),
        CommitOutcome(git_repo=tmp_path / "ok", label="ok", status="committed"),
    ]

    payload = build_json_payload(bundles, roots=[], mode="apply", commits=outcomes)

    assert payload["commits"] == [
        {
            "repo": str(repo),
            "label": "r",
            "status": "failed",
            "detail": "signing failed",
            "message": None,
            "files": [],
            "hashes": {},
        },
        {"repo": str(tmp_path / "ok"), "label": "ok", "status": "committed", "detail": None},
    ]
    assert _digest_lines_from_payload(payload) == [f"commit-fail {repo}  signing failed"]
    assert _retry_paths_from_payload(payload) == [str(repo)]


def _commit_failed_report(state: Path, repo: Path, files: list[str]) -> None:
    payload = _payload()
    payload["commits"] = [
        {
            "repo": str(repo),
            "label": "r",
            "status": "failed",
            "detail": "signing failed",
            "message": "chore(deps): bump",
            "files": files,
            "hashes": {f: content_hash(repo, f) for f in files},
        }
    ]
    _write_run_report(state, "20260101T000000.000000Z-apply", payload)


def _retry_commit_only(tmp_path: Path, init_git_repo: Any, monkeypatch: Any, extra_dirt: bool):
    from repo_ops_upgrade import post_apply_commit as pac

    repo = tmp_path / "r"
    init_git_repo(repo, commit=True)
    (repo / "package.json").write_text('{"name": "r"}\n', encoding="utf-8")
    run_git_add = __import__("subprocess").run
    run_git_add(["git", "-C", str(repo), "add", "package.json"], check=True)
    run_git_add(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    (repo / "package.json").write_text('{"name": "r", "version": "2"}\n', encoding="utf-8")
    if extra_dirt:
        (repo / "notes.txt").write_text("mine\n", encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _commit_failed_report(state, repo, ["package.json"])
    plans: list[Any] = []

    def _execute(plan: Any) -> Any:
        plans.append(plan)
        return pac.CommitOutcome(plan.git_repo, plan.label, "committed", files=plan.files)

    def _no_reapply(*_: Any, **__: Any) -> None:
        raise AssertionError("a commit retry must not re-apply")

    monkeypatch.setattr(pac, "execute_commit_plan", _execute)
    monkeypatch.setattr("repo_ops_upgrade.registry.apply_workspace", _no_reapply)
    main_apply(["--retry-failed"])
    return plans


def test_retry_recommits_a_commit_failed_repo_whose_only_dirt_is_its_own_edits(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any
) -> None:
    plans = _retry_commit_only(tmp_path, init_git_repo, monkeypatch, extra_dirt=False)
    assert [(p.message, p.files) for p in plans] == [("chore(deps): bump", ["package.json"])]


def test_retry_still_skips_a_commit_failed_repo_with_foreign_dirt(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any
) -> None:
    assert _retry_commit_only(tmp_path, init_git_repo, monkeypatch, extra_dirt=True) == []


def test_retry_failed_uses_newest_apply_run_and_skips_deleted_paths(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any, capsys: Any
) -> None:
    failed = tmp_path / "failed"
    gone = tmp_path / "gone"
    init_git_repo(failed, commit=True)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setattr("repo_ops_core.cli.default_roots", lambda: [str(tmp_path / "missing")])
    _write_run_report(
        state,
        "20260101T000000.000000Z-apply",
        _payload(
            _ws_json(str(failed), status="fail", error="boom"),
            _ws_json(str(gone), status="fail", error="boom"),
        ),
    )
    _write_run_report(state, "20260102T000000.000000Z-gates", _payload(_ws_json(str(failed))))
    captured: dict[str, Any] = {}

    def _fake_body(*, roots: list[str], report_path: Path, **_: Any) -> int:
        captured["roots"] = roots
        report_path.write_text("{}", encoding="utf-8")
        return 0

    with patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body):
        assert main_apply(["--retry-failed"]) == 0
    assert [Path(p).resolve() for p in captured["roots"]] == [failed.resolve()]
    assert f"skipping {gone}: no longer exists" in capsys.readouterr().err

    failed.rename(tmp_path / "moved")
    assert main_apply(["--retry-failed"]) == 0
    assert "no failed or skipped packages to retry" in capsys.readouterr().out


def test_retry_phase_timeout_fails_that_repo_and_others_still_finish(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any
) -> None:
    import subprocess

    from repo_ops_upgrade import orchestration
    from repo_ops_upgrade import run as run_mod

    repos = []
    for name in ("a", "b"):
        repo = tmp_path / name
        init_git_repo(repo, commit=True)
        (repo / "package.json").write_text(
            json.dumps({"name": name, "packageManager": "npm@10.0.0"}), encoding="utf-8"
        )
        (repo / "package-lock.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "pkg"], check=True)
        repos.append(repo)
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    _write_run_report(
        state,
        "20260101T000000.000000Z-apply",
        _payload(*(_ws_json(str(r), status="fail", error="boom") for r in repos)),
    )
    dirty_calls: dict[Path, int] = {}
    real_dirty = run_mod.is_git_dirty

    def _dirty(repo: Path) -> bool:
        dirty_calls[repo] = dirty_calls.get(repo, 0) + 1
        if repo == repos[0] and dirty_calls[repo] > 2:
            raise subprocess.TimeoutExpired("git", 1)
        return real_dirty(repo)

    def _process(wr: Any, **_: Any) -> Any:
        wr.status = "fail"
        wr.error = "boom"
        return wr

    monkeypatch.setattr(run_mod, "is_git_dirty", _dirty)
    monkeypatch.setattr(orchestration, "process_workspace", _process)
    main_apply(["--retry-failed", "--jobs", "1"])
    report = json.loads(
        max((state / "repo-ops" / "runs").glob("*-apply"), key=lambda p: p.name)
        .joinpath("report.json")
        .read_text()
    )
    errors = {ws["path"]: ws.get("error", "") for r in report["repos"] for ws in r["workspaces"]}
    assert "timed out" in errors[str(repos[0])]
    assert "timed out" not in errors[str(repos[1])]
