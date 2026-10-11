"""Fleet gate run: which gates failed, and what the exit code says."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from repo_ops_gates.gates import (
    GateResult,
    _failed_gate_names,
    _print_report,
    _record_path,
    _run_one,
    _status_hash,
)


def test_failed_gate_names_reads_gates_own_summary_lines():
    out = "\n".join(
        [
            "gate: ok  cargo build --locked  145ms  /var/tmp/gate/a.log",
            "gate: FAIL exit 1  uv run ruff check .  24ms",
            "gate: ok  actionlint  9ms  /var/tmp/gate/b.log",
            "gate: FAIL exit 101  sh -c cargo test --locked  3.1s",
            "gate: TIMEOUT after 30s  bun run test  (killed, not failed)",
            "gate: TIMEOUT after 1m0s  make test  (killed, not failed; allowed)",
        ]
    )
    assert _failed_gate_names(out) == [
        "uv run ruff check .",
        "sh -c cargo test --locked",
        "bun run test",
        "make test",
    ]


def test_a_repo_with_no_gate_is_not_counted_as_broken(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: ([], "", 0))
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "none"
    assert result.failed == []


def test_a_failing_gate_is_named(tmp_path: Path, monkeypatch, make_proc):
    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: (["lint", "test"], "", 0)
    )
    monkeypatch.setattr(
        "repo_ops_gates.gates.run",
        lambda *a, **k: make_proc(returncode=1, stdout="gate: FAIL exit 1  bun run lint  2s\n"),
    )
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "fail"
    assert result.failed == ["bun run lint"]
    assert result.logs == []


def _write_record(
    repo: Path,
    head: str,
    finished: datetime,
    gates: list[dict[str, object]],
    *,
    schema: int = 2,
    status: str | None = None,
) -> None:
    # The suite points XDG_STATE_HOME at a directory inside the checkout. Creating
    # the record there is an untracked path, so the hash taken before the write
    # is not the hash a reader sees. Keep the state directory beside the repo.
    state = repo.parent / "gate-record-state"
    state.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_STATE_HOME"] = str(state)
    path = _record_path(str(repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {
        "schema": schema,
        "root": str(repo),
        "head": head,
        "finished": finished.isoformat(),
        "gates": gates,
    }
    if schema == 2:
        body["status"] = _status_hash(repo) if status is None else status
    path.write_text(json.dumps(body), encoding="utf-8")


def test_a_matching_record_names_timeout_and_interrupted_with_logs(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo, capsys
):
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    log = "/var/tmp/gate/sleep-30.log"
    _write_record(
        tmp_path,
        head,
        datetime.now().astimezone() + timedelta(hours=1),
        [
            {
                "name": "lint",
                "display": "bun run lint",
                "status": "ok",
                "log": "/var/tmp/gate/lint.log",
            },
            {
                "name": "test",
                "display": "bun run test",
                "status": "timeout",
                "log": log,
            },
            {"display": "cargo test", "status": "interrupted", "log": "/var/tmp/gate/cargo.log"},
            {"display": "uv run ruff check .", "status": "fail", "log": "/var/tmp/gate/ruff.log"},
            {"name": "vuln", "status": "error", "log": "/var/tmp/gate/vuln.log"},
            {"name": "e2e", "display": "playwright", "status": "skipped"},
            {"name": "missing", "display": "gone", "status": "not-found"},
        ],
    )
    seen: dict[str, object] = {}

    def fake_run(cmd, cwd, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return make_proc(returncode=124, stderr="gate: TIMEOUT after 30s  bun run test\n")

    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing",
        lambda repo, *, e2e: (["lint", "test"], str(tmp_path), 0),
    )
    monkeypatch.setattr("repo_ops_gates.gates.run", fake_run)
    result = _run_one(tmp_path, [str(tmp_path)])
    assert seen["cmd"] == ["gate", "--timeout", "0", "run", "lint", "test"]
    assert seen["timeout"] == 1800
    assert result.status == "fail"
    assert result.failed == ["bun run test", "cargo test", "uv run ruff check .", "vuln", "gone"]
    assert result.logs == [
        log,
        "/var/tmp/gate/cargo.log",
        "/var/tmp/gate/ruff.log",
        "/var/tmp/gate/vuln.log",
        "",
    ]
    assert result.to_json()["logs"] == result.logs
    _print_report([result], elapsed=1.0)
    report = capsys.readouterr().out
    assert f"bun run test  {log}" in report
    assert "cargo test  /var/tmp/gate/cargo.log" in report


def test_a_record_from_another_run_falls_back_to_the_summary(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
):
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: (["lint"], str(tmp_path), 0)
    )
    monkeypatch.setattr(
        "repo_ops_gates.gates.run",
        lambda *a, **k: make_proc(returncode=1, stdout="gate: FAIL exit 1  bun run lint  2s\n"),
    )

    _write_record(
        tmp_path,
        "0" * 40,
        datetime.now().astimezone() + timedelta(hours=1),
        [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
    )
    wrong_head = _run_one(tmp_path, [str(tmp_path)])
    assert wrong_head.failed == ["bun run lint"]
    assert wrong_head.logs == []

    _write_record(
        tmp_path,
        head,
        datetime.now().astimezone() - timedelta(hours=1),
        [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
    )
    stale = _run_one(tmp_path, [str(tmp_path)])
    assert stale.failed == ["bun run lint"]
    assert stale.logs == []
    assert stale.status == "fail"


def test_a_passing_process_stays_passing_when_a_record_names_a_timeout(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
):
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    _write_record(
        tmp_path,
        head,
        datetime.now().astimezone() + timedelta(hours=1),
        [{"display": "bun run test", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
    )
    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: (["test"], str(tmp_path), 0)
    )
    monkeypatch.setattr("repo_ops_gates.gates.run", lambda *a, **k: make_proc(returncode=0))
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "ok"
    assert result.failed == []


def test_to_json_round_trips_the_fields_the_report_reads():
    r = GateResult(repo=Path("/x"), label="x", status="fail", failed=["lint"], elapsed_s=1.234)
    payload = r.to_json()
    assert payload["status"] == "fail"
    assert payload["failed"] == ["lint"]
    assert payload["elapsed_s"] == 1.23
    assert payload["e2e_left_out"] == 0


def test_status_hash_is_empty_status_and_diff_until_the_tree_changes(
    tmp_path: Path, init_git_repo
) -> None:
    init_git_repo(tmp_path, commit=True)
    clean = _status_hash(tmp_path)
    assert clean == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    (tmp_path / "note").write_text("hello\n", encoding="utf-8")
    dirty = _status_hash(tmp_path)
    assert dirty is not None and dirty != clean
    (tmp_path / "note").write_text("other\n", encoding="utf-8")
    assert _status_hash(tmp_path) != dirty


def test_a_record_for_another_tree_falls_back_to_timeout_text(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
) -> None:
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    (tmp_path / "note").write_text("hello\n", encoding="utf-8")
    _write_record(
        tmp_path,
        head,
        datetime.now().astimezone() + timedelta(hours=1),
        [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
        status="0" * 64,
    )
    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: (["test"], str(tmp_path), 0)
    )
    monkeypatch.setattr(
        "repo_ops_gates.gates.run",
        lambda *a, **k: make_proc(
            returncode=124,
            stderr="gate: TIMEOUT after 30s  bun run test  (killed, not failed)\n",
        ),
    )
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.failed == ["bun run test"]
    assert result.logs == []

    _write_record(
        tmp_path,
        head,
        datetime.now().astimezone() + timedelta(hours=1),
        [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
        schema=1,
    )
    old = _run_one(tmp_path, [str(tmp_path)])
    assert old.failed == ["bun run test"]
    assert old.logs == []


def test_e2e_gates_are_left_out_unless_requested(tmp_path: Path, monkeypatch, make_proc) -> None:
    listing = json.dumps(
        {
            "root": str(tmp_path),
            "gates": [
                {"name": "lint"},
                {"name": "e2e", "e2e": True, "skipped": True},
            ],
        }
    )
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        calls.append(cmd)
        if "--json" in cmd:
            return make_proc(returncode=0, stdout=listing)
        return make_proc(returncode=0)

    monkeypatch.setattr("repo_ops_gates.gates.run", fake_run)
    skipped = _run_one(tmp_path, [str(tmp_path)])
    assert calls[-1] == ["gate", "--timeout", "0", "run", "lint"]
    assert skipped.status == "ok"
    assert skipped.e2e_left_out == 1
    assert skipped.gates == ["lint"]

    ran = _run_one(tmp_path, [str(tmp_path)], e2e=True)
    assert calls[-1] == ["gate", "--timeout", "0", "run", "lint", "e2e"]
    assert ran.e2e_left_out == 0
    assert ran.gates == ["lint", "e2e"]


def test_a_repo_whose_only_gate_is_e2e_counts_as_gated(
    tmp_path: Path, monkeypatch, make_proc
) -> None:
    listing = json.dumps(
        {"root": str(tmp_path), "gates": [{"name": "e2e", "e2e": True, "skipped": True}]}
    )
    monkeypatch.setattr(
        "repo_ops_gates.gates.run",
        lambda *a, **k: make_proc(returncode=0, stdout=listing),
    )
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "ok"
    assert result.gates == []
    assert result.e2e_left_out == 1


def test_summary_counts_the_repo_and_names_e2e_gates_left_out(capsys) -> None:
    results = [
        GateResult(repo=Path("/a"), label="a", status="ok", e2e_left_out=2),
        GateResult(repo=Path("/b"), label="b", status="none"),
    ]
    _print_report(results, elapsed=1)
    out = capsys.readouterr().out
    assert "repos gated       1" in out
    assert "passing           1" in out
    assert "e2e left out      2" in out
