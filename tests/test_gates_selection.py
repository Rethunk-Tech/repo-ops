"""Direct tests for gate-selection branches in repo_ops_gates.gates."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from repo_ops_gates.gates import (
    GateResult,
    _gate_listing,
    _print_report,
    _report_document,
    _run_one,
    build_gates_parser,
    main_gates,
)

GATE_BODY = """
import json
import sys
from pathlib import Path

cfg_path = Path(".fake-gate.json")
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
args = sys.argv[1:]
if args == ["--json"]:
    if "listing_raw" in cfg:
        sys.stdout.write(cfg["listing_raw"])
        raise SystemExit(cfg.get("listing_code", 0))
    listing = dict(cfg.get("listing", {"gates": []}))
    listing.setdefault("root", str(Path.cwd()))
    json.dump(listing, sys.stdout)
    raise SystemExit(cfg.get("listing_code", 0))
if args[:2] == ["--json", "record"]:
    if "record_raw" in cfg:
        sys.stdout.write(cfg["record_raw"])
        raise SystemExit(0)
    json.dump(cfg.get("record", {"fresh": True}), sys.stdout)
    raise SystemExit(0)
sys.stdout.write(cfg.get("run_stdout", ""))
sys.stderr.write(cfg.get("run_stderr", ""))
raise SystemExit(cfg.get("run_code", 0))
"""


def _install_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: dict) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    listing = cfg.get("listing")
    if isinstance(listing, dict) and listing.get("root") == "$repo":
        cfg = {**cfg, "listing": {**listing, "root": str(repo)}}
    (repo / ".fake-gate.json").write_text(json.dumps(cfg))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gate = bindir / "gate"
    gate.write_text(f"#!{sys.executable}\n{GATE_BODY}")
    gate.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    return repo


@pytest.mark.parametrize(
    ("cfg", "e2e", "want"),
    [
        (
            {
                "listing": {
                    "gates": [{"name": "unit"}, {"name": "e2e", "skipped": True}],
                    "root": "r",
                }
            },
            False,
            (["unit"], "r", 1),
        ),
        (
            {
                "listing": {
                    "gates": [{"name": "unit"}, {"name": "e2e", "skipped": True}],
                    "root": "r",
                }
            },
            True,
            (["unit", "e2e"], "r", 0),
        ),
        ({"listing": {"gates": [], "root": "r"}}, False, ([], "r", 0)),
        ({"listing_code": 1}, False, ([], "", 0)),
        ({"listing_raw": "not-json"}, False, ([], "", 0)),
        ({"listing": {"gates": [{"skipped": True}], "root": "r"}}, False, ([], "r", 1)),
        ({"listing": {"gates": [{}], "root": "r"}}, False, ([], "", 0)),
        ({"listing": {"gates": [{"name": "u"}], "root": 1}}, False, (["u"], "", 0)),
        ({"listing": {"gates": None, "root": "r"}}, False, ([], "r", 0)),
        ({"listing": {"root": "r"}}, False, ([], "r", 0)),
    ],
)
def test_gate_listing_selects_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cfg: dict,
    e2e: bool,
    want: tuple[list[str], str, int],
) -> None:
    repo = _install_gate(tmp_path, monkeypatch, cfg)
    assert _gate_listing(repo, e2e=e2e) == want


@pytest.mark.parametrize(
    ("cfg", "kwargs", "expect"),
    [
        ({"listing": {"gates": [], "root": "$repo"}}, {}, {"status": "none", "e2e_left_out": 0}),
        (
            {"listing": {"gates": [{"name": "e2e", "skipped": True}], "root": "$repo"}},
            {},
            {"status": "ok", "e2e_left_out": 1, "gates": []},
        ),
        (
            {"listing": {"gates": [{"name": "unit"}], "root": "$repo"}, "run_code": 0},
            {},
            {"status": "ok", "gates": ["unit"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record": {"fresh": True},
            },
            {"skip_fresh": True},
            {"status": "skipped", "skip_reason": "fresh", "gates": ["unit"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record": {"reason": "partial"},
                "run_code": 0,
            },
            {"skip_fresh": True},
            {
                "status": "ok",
                "record_partial": True,
                "gate_record_reason": "partial",
            },
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record": {"reason": "failed"},
                "run_code": 0,
            },
            {"skip_fresh": True},
            {"status": "ok", "gate_record_reason": "failed", "record_partial": False},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record": {"reason": "missing"},
                "run_code": 0,
            },
            {"skip_fresh": True},
            {"status": "ok", "gate_record_reason": "missing"},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record_raw": "not-json",
                "run_code": 0,
            },
            {"skip_fresh": True},
            {"status": "ok", "gate_record_reason": "stale"},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": ""},
                "run_code": 0,
            },
            {"skip_fresh": True},
            {"status": "ok", "gate_record_reason": "missing"},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record": {"fresh": False},
                "run_code": 0,
            },
            {"skip_fresh": True},
            {"status": "ok", "gate_record_reason": "stale"},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "record": {"reason": "bogus"},
                "run_code": 0,
            },
            {"skip_fresh": True},
            {"status": "ok", "gate_record_reason": "stale"},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stdout": (
                    "gate: FAIL exit 1  bun test  1.2s\n"
                    "gate: TIMEOUT after 30s  bun e2e  (killed, not failed)\n"
                    "gate: FAIL exit 2\n"
                    "gate: TIMEOUT after 1s\n"
                    "gate: TIMEOUT\n"
                ),
                "record": {"reason": "stale"},
            },
            {},
            {"status": "fail", "failed": ["bun test", "bun e2e", "exit 2", "after 1s"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stderr": "compiler exploded\n",
                "record": {
                    "fresh": True,
                    "finished": "2099-01-01T00:00:00+00:00",
                    "gates": [
                        {"status": "fail", "display": "from-display", "log": "/tmp/a.log"},
                        {"status": "timeout", "name": "from-name", "log": "/tmp/b.log"},
                        {"status": "error", "argv": ["go", "test"], "log": 123},
                        {"status": "interrupted"},
                        {"status": "ok", "name": "passed"},
                        "not-a-dict",
                    ],
                },
            },
            {},
            {
                "status": "fail",
                "failed": ["from-display", "from-name", "go test"],
                "logs": ["/tmp/a.log", "/tmp/b.log", ""],
            },
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stdout": "gate: FAIL exit 1  scraped  0.1s\n",
                "record": {
                    "fresh": True,
                    "finished": "2099-01-01T00:00:00",
                    "gates": [{"status": "fail", "name": "ignored"}],
                },
            },
            {},
            {"status": "fail", "failed": ["scraped"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stdout": "gate: FAIL exit 1  scraped  0.1s\n",
                "record": {
                    "fresh": True,
                    "finished": 1,
                    "gates": [{"status": "fail", "name": "x"}],
                },
            },
            {},
            {"status": "fail", "failed": ["scraped"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stdout": "gate: FAIL exit 1  scraped  0.1s\n",
                "record": {"fresh": True, "finished": "not-a-date"},
            },
            {},
            {"status": "fail", "failed": ["scraped"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stdout": "gate: FAIL exit 1  scraped  0.1s\n",
                "record": {
                    "fresh": True,
                    "finished": "2099-01-01T00:00:00+00:00",
                    "gates": "nope",
                },
            },
            {},
            {"status": "fail", "failed": ["scraped"]},
        ),
        (
            {
                "listing": {"gates": [{"name": "unit"}], "root": "$repo"},
                "run_code": 1,
                "run_stdout": "gate: FAIL exit 1  scraped  0.1s\n",
                "record": {
                    "fresh": True,
                    "finished": "2000-01-01T00:00:00+00:00",
                    "gates": [{"status": "fail", "name": "old"}],
                },
            },
            {},
            {"status": "fail", "failed": ["scraped"]},
        ),
    ],
)
def test_run_one_gate_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cfg: dict,
    kwargs: dict,
    expect: dict,
) -> None:
    repo = _install_gate(tmp_path, monkeypatch, cfg)
    result = _run_one(repo, [str(tmp_path)], **kwargs)
    for key, value in expect.items():
        assert getattr(result, key) == value


def test_run_one_errors_when_gate_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = _run_one(repo, [str(tmp_path)])
    assert result.status == "error"
    assert result.detail


def test_main_gates_without_gate_returns_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert main_gates(["--jobs", "1", str(tmp_path)]) == 2


def test_main_gates_empty_root_exits_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_gate(tmp_path, monkeypatch, {"listing": {"gates": []}})
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main_gates(["--jobs", "1", str(empty)]) == 0


def test_report_helpers(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    args = build_gates_parser().parse_args(["--e2e", "--skip-fresh"])
    assert args.e2e is True
    assert args.skip_fresh is True

    ok = GateResult(repo=tmp_path, label="a/ok", elapsed_s=1.5, gates=["unit"])
    skipped = GateResult(repo=tmp_path, label="a/skip", status="skipped", skip_reason="fresh")
    none = GateResult(repo=tmp_path, label="a/none", status="none")
    failed = GateResult(
        repo=tmp_path,
        label="a/fail",
        status="fail",
        failed=["lint"],
        logs=["/tmp/lint.log"],
        elapsed_s=3.0,
        e2e_left_out=1,
        record_partial=True,
        gate_record_reason="partial",
    )
    errored = GateResult(
        repo=tmp_path, label="a/err", status="error", detail="boom\nmore", elapsed_s=0.2
    )
    _print_report([ok, skipped, none, failed, errored], elapsed=4.0)
    out = capsys.readouterr().out
    assert "FAIL a/fail" in out
    assert "lint  /tmp/lint.log" in out
    assert "FAIL a/err" in out
    assert "boom" in out
    assert "slowest:" in out

    payload = failed.to_json()
    assert payload["record_partial"] is True
    assert payload["gate_record_reason"] == "partial"
    assert "skip_reason" not in payload
    assert skipped.to_json()["skip_reason"] == "fresh"

    doc = _report_document(
        [ok, skipped, none, failed, errored],
        roots=["/x"],
        elapsed=4.0,
        excluded=2,
        exit_code=1,
    )
    assert doc["skipped"] == 1
    assert doc["record_partial"] == 1
    assert doc["e2e_left_out"] == 1
    assert doc["excluded"] == 2
    assert doc["exit_code"] == 1
