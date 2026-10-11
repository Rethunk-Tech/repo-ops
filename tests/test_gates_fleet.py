"""Fleet gate run: which gates failed, and what the exit code says."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from repo_ops_gates.gates import (
    GateResult,
    _failed_gate_names,
    _print_report,
    _record_path,
    _run_one,
)


def test_failed_gate_names_reads_gates_own_summary_lines():
    out = "\n".join(
        [
            "gate: ok  cargo build --locked  145ms  /var/tmp/gate/a.log",
            "gate: FAIL exit 1  uv run ruff check .  24ms",
            "gate: ok  actionlint  9ms  /var/tmp/gate/b.log",
            "gate: FAIL exit 101  sh -c cargo test --locked  3.1s",
        ]
    )
    assert _failed_gate_names(out) == ["uv run ruff check .", "sh -c cargo test --locked"]


def test_a_repo_with_no_gate_is_not_counted_as_broken(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("repo_ops_gates.gates._gate_listing", lambda repo: ([], ""))
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "none"
    assert result.failed == []


def test_a_failing_gate_is_named(tmp_path: Path, monkeypatch, make_proc):
    monkeypatch.setattr("repo_ops_gates.gates._gate_listing", lambda repo: (["lint", "test"], ""))
    monkeypatch.setattr(
        "repo_ops_gates.gates.run",
        lambda *a, **k: make_proc(returncode=1, stdout="gate: FAIL exit 1  bun run lint  2s\n"),
    )
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "fail"
    assert result.failed == ["bun run lint"]
    assert result.logs == []


def _write_record(
    repo: Path, head: str, finished: datetime, gates: list[dict[str, object]]
) -> None:
    path = _record_path(str(repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "root": str(repo),
                "head": head,
                "finished": finished.isoformat(),
                "gates": gates,
            }
        ),
        encoding="utf-8",
    )


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
        "repo_ops_gates.gates._gate_listing", lambda repo: (["lint", "test"], str(tmp_path))
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
        "repo_ops_gates.gates._gate_listing", lambda repo: (["lint"], str(tmp_path))
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
        "repo_ops_gates.gates._gate_listing", lambda repo: (["test"], str(tmp_path))
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
