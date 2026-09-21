"""Fleet gate run: which gates failed, and what the exit code says."""

from __future__ import annotations

from pathlib import Path

from repo_ops_gates.gates import GateResult, _failed_gate_names, _run_one


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
    monkeypatch.setattr("repo_ops_gates.gates._gate_names", lambda repo: [])
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "none"
    assert result.failed == []


def test_a_failing_gate_is_named(tmp_path: Path, monkeypatch, make_proc):
    monkeypatch.setattr("repo_ops_gates.gates._gate_names", lambda repo: ["lint", "test"])
    monkeypatch.setattr(
        "repo_ops_gates.gates.run",
        lambda *a, **k: make_proc(returncode=1, stdout="gate: FAIL exit 1  bun run lint  2s\n"),
    )
    result = _run_one(tmp_path, [str(tmp_path)])
    assert result.status == "fail"
    assert result.failed == ["bun run lint"]


def test_to_json_round_trips_the_fields_the_report_reads():
    r = GateResult(repo=Path("/x"), label="x", status="fail", failed=["lint"], elapsed_s=1.234)
    payload = r.to_json()
    assert payload["status"] == "fail"
    assert payload["failed"] == ["lint"]
    assert payload["elapsed_s"] == 1.23
