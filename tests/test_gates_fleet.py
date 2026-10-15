"""Fleet gate run: which gates failed, and what the exit code says."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from repo_ops_gates.gates import (
    GateResult,
    _failed_gate_names,
    _matching_record,
    _print_report,
    _report_document,
    _run_one,
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


_RECORD_CMD = ["gate", "--json", "record"]


def _record_view(
    head: str,
    finished: datetime,
    gates: list[dict[str, object]],
    *,
    schema: int = 2,
    stale: bool = False,
    exit_code: int | None = None,
    partial: bool | None = None,
) -> dict[str, object]:
    """What ``gate --json record`` prints, with gate's own order for ``reason``."""
    body: dict[str, object] = {
        "schema": schema,
        "root": "/repo",
        "head": head,
        "finished": finished.isoformat(),
        "gates": gates,
    }
    if schema == 2:
        body["status"] = "0" * 64
    if exit_code is not None:
        body["exit"] = exit_code
    if partial is not None:
        body["partial"] = partial
    reason = None
    if stale:
        reason = "stale"
    elif exit_code != 0:
        reason = "failed"
    elif partial:
        reason = "partial"
    current = {"head": head, "status": "0" * 64, "dirty": False}
    return {**body, "fresh": reason is None, "reason": reason, "current": current}


def _serve(monkeypatch, make_proc, state: dict[str, object], inner=None) -> None:
    """Answer ``gate --json record`` from ``state["view"]``; anything else goes to *inner*."""

    def fake_run(cmd, cwd, timeout):
        if cmd == _RECORD_CMD:
            view = state.get("view") or {"fresh": False, "reason": "missing"}
            assert isinstance(view, dict)
            return make_proc(returncode=0 if view["fresh"] else 1, stdout=json.dumps(view))
        if inner is None:
            return make_proc(returncode=0)
        return inner(cmd, cwd, timeout)

    monkeypatch.setattr("repo_ops_gates.gates.run", fake_run)


def test_a_matching_record_names_timeout_and_interrupted_with_logs(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo, capsys
):
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    log = "/var/tmp/gate/sleep-30.log"
    state: dict[str, object] = {}
    state["view"] = _record_view(
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
    _serve(monkeypatch, make_proc, state, fake_run)
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
    state: dict[str, object] = {}
    _serve(
        monkeypatch,
        make_proc,
        state,
        lambda *a, **k: make_proc(returncode=1, stdout="gate: FAIL exit 1  bun run lint  2s\n"),
    )

    state["view"] = _record_view(
        "0" * 40,
        datetime.now().astimezone() + timedelta(hours=1),
        [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
        stale=True,
    )
    wrong_head = _run_one(tmp_path, [str(tmp_path)])
    assert wrong_head.failed == ["bun run lint"]
    assert wrong_head.logs == []

    state["view"] = _record_view(
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
    state: dict[str, object] = {
        "view": _record_view(
            head,
            datetime.now().astimezone() + timedelta(hours=1),
            [{"display": "bun run test", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
        )
    }
    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: (["test"], str(tmp_path), 0)
    )
    _serve(monkeypatch, make_proc, state)
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


def test_matching_record_reports_the_reason_gate_record_gives(
    tmp_path: Path, monkeypatch, make_proc
) -> None:
    state: dict[str, object] = {}
    _serve(monkeypatch, make_proc, state)
    root = str(tmp_path)
    finished = datetime.now().astimezone()
    gates: list[dict[str, object]] = [{"name": "lint", "status": "ok"}]

    assert _matching_record(root) == (None, "missing")
    assert _matching_record("") == (None, "missing")

    state["view"] = _record_view("h", finished, gates, exit_code=0)
    record, reason = _matching_record(root)
    assert reason is None
    assert record is not None and record["gates"] == gates
    assert not {"fresh", "reason", "current"} & record.keys()

    state["view"] = _record_view("h", finished, gates, exit_code=0, partial=True)
    assert _matching_record(root)[1] == "partial"
    state["view"] = _record_view("h", finished, gates, exit_code=1, partial=True)
    assert _matching_record(root)[1] == "failed"
    state["view"] = _record_view("h", finished, gates, exit_code=1, stale=True)
    assert _matching_record(root) == (None, "stale")


def test_an_unreadable_gate_record_is_stale(tmp_path: Path, monkeypatch, make_proc) -> None:
    root = str(tmp_path)

    def serve(stdout: str) -> None:
        monkeypatch.setattr(
            "repo_ops_gates.gates.run", lambda *a, **k: make_proc(returncode=1, stdout=stdout)
        )

    serve("not json")
    assert _matching_record(root) == (None, "stale")
    serve(json.dumps({"gates": [{"argv": ["record"]}]}))
    assert _matching_record(root) == (None, "stale")
    serve(json.dumps({"fresh": False, "reason": "bogus"}))
    assert _matching_record(root) == (None, "stale")

    def absent(*a, **k):
        raise FileNotFoundError("gate")

    monkeypatch.setattr("repo_ops_gates.gates.run", absent)
    assert _matching_record(root) == (None, "stale")


def test_a_record_for_another_tree_falls_back_to_timeout_text(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
) -> None:
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    (tmp_path / "note").write_text("hello\n", encoding="utf-8")
    state: dict[str, object] = {
        "view": _record_view(
            head,
            datetime.now().astimezone() + timedelta(hours=1),
            [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
            stale=True,
        )
    }
    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing", lambda repo, *, e2e: (["test"], str(tmp_path), 0)
    )
    _serve(
        monkeypatch,
        make_proc,
        state,
        lambda *a, **k: make_proc(
            returncode=124,
            stderr="gate: TIMEOUT after 30s  bun run test  (killed, not failed)\n",
        ),
    )
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.failed == ["bun run test"]
    assert result.logs == []

    state["view"] = _record_view(
        head,
        datetime.now().astimezone() + timedelta(hours=1),
        [{"display": "sleep 30", "status": "timeout", "log": "/var/tmp/gate/sleep.log"}],
        schema=1,
        stale=True,
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
    assert "skipped           0" in out
    assert "e2e left out      2" in out


def test_summary_counts_a_skipped_repo_apart_from_ones_that_ran(capsys) -> None:
    results = [
        GateResult(repo=Path("/a"), label="a", status="skipped"),
        GateResult(repo=Path("/b"), label="b", status="ok"),
        GateResult(repo=Path("/c"), label="c", status="none"),
    ]
    _print_report(results, elapsed=1)
    out = capsys.readouterr().out
    assert "repos gated       1" in out
    assert "passing           1" in out
    assert "skipped           1" in out
    assert "no gate declared  1" in out


def test_skip_fresh_does_not_run_a_passing_tree_and_reruns_a_dirty_one(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
) -> None:
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        calls.append(list(cmd))
        return make_proc(returncode=0)

    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing",
        lambda repo, *, e2e: (["lint", "e2e"] if e2e else ["lint"], str(tmp_path), 0 if e2e else 1),
    )
    state: dict[str, object] = {}
    _serve(monkeypatch, make_proc, state, fake_run)
    state["view"] = _record_view(
        head,
        datetime.now().astimezone(),
        [{"name": "lint", "display": "bun run lint", "status": "ok"}],
        exit_code=0,
    )

    fresh = _run_one(tmp_path, [str(tmp_path)], skip_fresh=True)
    assert calls == []
    assert fresh.status == "skipped"
    assert fresh.to_json()["status"] == "skipped"
    assert fresh.to_json()["skip_reason"] == "fresh"
    assert "gate_record_reason" not in fresh.to_json()
    assert fresh.gates == ["lint"]

    with_e2e = _run_one(tmp_path, [str(tmp_path)], e2e=True, skip_fresh=True)
    assert calls == []
    assert with_e2e.status == "skipped"
    assert with_e2e.to_json()["skip_reason"] == "fresh"

    without_flag = _run_one(tmp_path, [str(tmp_path)])
    assert calls == [["gate", "--timeout", "0", "run", "lint"]]
    assert without_flag.status == "ok"
    assert "skip_reason" not in without_flag.to_json()
    assert "gate_record_reason" not in without_flag.to_json()
    calls.clear()

    state["view"] = _record_view(
        head,
        datetime.now().astimezone(),
        [{"name": "lint", "display": "bun run lint", "status": "ok"}],
        exit_code=0,
        stale=True,
    )
    dirty = _run_one(tmp_path, [str(tmp_path)], e2e=True, skip_fresh=True)
    assert calls == [["gate", "--timeout", "0", "run", "lint", "e2e"]]
    assert dirty.status == "ok"
    assert "skip_reason" not in dirty.to_json()
    assert dirty.to_json()["gate_record_reason"] == "stale"


def test_skip_fresh_still_runs_a_failed_or_missing_record(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
) -> None:
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        calls.append(list(cmd))
        return make_proc(returncode=1, stdout="gate: FAIL exit 1  bun run lint  2s\n")

    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing",
        lambda repo, *, e2e: (["lint"], str(tmp_path), 0),
    )
    state: dict[str, object] = {}
    _serve(monkeypatch, make_proc, state, fake_run)

    missing = _run_one(tmp_path, [str(tmp_path)], skip_fresh=True)
    assert calls == [["gate", "--timeout", "0", "run", "lint"]]
    assert missing.status == "fail"
    assert missing.to_json()["gate_record_reason"] == "missing"
    calls.clear()

    state["view"] = _record_view(
        head,
        datetime.now().astimezone(),
        [{"display": "bun run lint", "status": "fail"}],
        exit_code=1,
    )
    failed = _run_one(tmp_path, [str(tmp_path)], skip_fresh=True)
    assert calls == [["gate", "--timeout", "0", "run", "lint"]]
    assert failed.status == "fail"
    assert failed.to_json()["gate_record_reason"] == "failed"


def test_skip_fresh_reruns_a_partial_pass_and_skips_a_full_one(
    tmp_path: Path, monkeypatch, make_proc, init_git_repo
) -> None:
    head = init_git_repo(tmp_path, commit=True)
    assert head is not None
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        calls.append(list(cmd))
        return make_proc(returncode=0)

    monkeypatch.setattr(
        "repo_ops_gates.gates._gate_listing",
        lambda repo, *, e2e: (["lint"], str(tmp_path), 0),
    )
    state: dict[str, object] = {}
    _serve(monkeypatch, make_proc, state, fake_run)

    state["view"] = _record_view(
        head,
        datetime.now().astimezone(),
        [{"name": "lint", "display": "bun run lint", "status": "ok"}],
        exit_code=0,
        partial=True,
    )
    partial = _run_one(tmp_path, [str(tmp_path)], skip_fresh=True)
    assert calls == [["gate", "--timeout", "0", "run", "lint"]]
    assert partial.status == "ok"
    assert partial.status != "skipped"
    assert partial.to_json()["record_partial"] is True
    assert partial.to_json()["gate_record_reason"] == "partial"
    assert "skip_reason" not in partial.to_json()
    calls.clear()

    state["view"] = _record_view(
        head,
        datetime.now().astimezone(),
        [{"name": "lint", "display": "bun run lint", "status": "ok"}],
        exit_code=0,
        partial=False,
    )
    full = _run_one(tmp_path, [str(tmp_path)], skip_fresh=True)
    assert calls == []
    assert full.status == "skipped"
    assert full.to_json()["skip_reason"] == "fresh"
    assert "gate_record_reason" not in full.to_json()
    assert "record_partial" not in full.to_json()
    report = _report_document(
        [partial, full],
        roots=[str(tmp_path)],
        elapsed=0,
        excluded=0,
        exit_code=0,
    )
    assert report["record_partial"] == 1
