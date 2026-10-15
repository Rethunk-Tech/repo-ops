"""Fleet-wide gate run: every repo's declared gate, reported together.

``apply`` verifies only what it changed, so a repo nobody has touched can sit
broken indefinitely with nothing saying so. This walks the same roots and runs
each project's own gate, which is the check its CI would run.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from repo_ops_core.cli import add_scan_arguments, resolve_scan_roots, wire_verbose
from repo_ops_core.config import load_global_config
from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.discovery import discover_repos
from repo_ops_core.exclude import filter_repo_paths, merge_exclude_patterns
from repo_ops_core.filesystem import new_run_dir, write_json_atomic
from repo_ops_core.git import repo_label
from repo_ops_core.parallel import run_bounded_pool
from repo_ops_core.process import run
from repo_ops_core.tee import tee_stdout_stderr

# A gate that runs a real test suite is slow; this is a ceiling for a hang, not
# a budget. The per-gate timeout is disabled so this one is the only authority.
TIMEOUT_GATE = 1800
TIMEOUT_LIST = 60

# ``gate`` separates its summary columns by two or more spaces.
_FIELD_SPLIT = re.compile(r"\s{2,}")

# Status words from gate's result record that mean the gate did not pass.
# ``INTERRUPTED`` is not a ``gate: FAIL`` line. ``TIMEOUT`` is scraped from
# the text when the record is not this run.
_RECORD_FAILURES = frozenset({"fail", "timeout", "interrupted", "not-found", "error"})


@dataclass
class GateResult:
    repo: Path
    label: str
    status: str = "ok"  # ok | fail | none | error | skipped
    gates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    detail: str = ""
    e2e_left_out: int = 0
    skip_reason: str = ""
    record_partial: bool = False
    gate_record_reason: str = ""

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "repo": str(self.repo),
            "label": self.label,
            "status": self.status,
            "gates": self.gates,
            "failed": self.failed,
            "logs": self.logs,
            "elapsed_s": round(self.elapsed_s, 2),
            "detail": self.detail,
            "e2e_left_out": self.e2e_left_out,
        }
        if self.skip_reason:
            payload["skip_reason"] = self.skip_reason
        if self.record_partial:
            payload["record_partial"] = True
        if self.gate_record_reason:
            payload["gate_record_reason"] = self.gate_record_reason
        return payload


def _gate_listing(repo: Path, *, e2e: bool) -> tuple[list[str], str, int]:
    """Gate names to run, the project root, and how many e2e gates were left out.

    ``gate --json`` executes nothing and exits 0 even where it detects nothing,
    so it doubles as the availability probe and the gate set. The root is the
    path gate hashes into its result record; an empty root means the listing
    did not name one. A default listing marks e2e gates ``skipped``; those stay
    out unless *e2e* is set, the same choice ``verify`` makes.
    """
    proc = run(["gate", "--json"], repo, timeout=TIMEOUT_LIST)
    if proc.returncode != 0:
        return [], "", 0
    try:
        payload = json.loads(proc.stdout)
        names: list[str] = []
        left_out = 0
        for gate in payload.get("gates") or []:
            if gate.get("skipped") and not e2e:
                left_out += 1
                continue
            names.append(gate["name"])
        root = payload.get("root") or ""
    except (ValueError, AttributeError, TypeError, KeyError):
        return [], "", 0
    return names, root if isinstance(root, str) else "", left_out


def _gate_label(gate: dict[str, object]) -> str:
    display = gate.get("display")
    if isinstance(display, str) and display:
        return display
    name = gate.get("name")
    if isinstance(name, str) and name:
        return name
    argv = gate.get("argv")
    if isinstance(argv, list) and argv and all(isinstance(part, str) for part in argv):
        return " ".join(argv)
    return ""


def _matching_record(root: str) -> tuple[dict[str, object] | None, str | None]:
    """Schema-2 record for this tree, and why it is not a fresh pass.

    Asks ``gate --json record`` at *root*. The second value is its ``reason``:
    ``missing``, ``stale``, ``partial``, ``failed``, or None when the record is a
    fresh non-partial pass. ``gate`` exits non-zero for every reason but fresh, so
    only its JSON is read. A ``gate`` that is absent, times out, or prints
    something that is not a verdict is ``stale``: the record is unreadable, not
    missing.
    """
    if not root:
        return None, "missing"
    try:
        proc = run(["gate", "--json", "record"], root, timeout=TIMEOUT_LIST)
        view = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, "stale"
    if not isinstance(view, dict):
        return None, "stale"
    reason = view.get("reason")
    if reason in ("missing", "stale"):
        return None, reason
    if reason is None and view.get("fresh") is not True:
        return None, "stale"
    if reason is not None and reason not in ("partial", "failed"):
        return None, "stale"
    record = {k: v for k, v in view.items() if k not in ("fresh", "reason", "current")}
    return record, reason


def _current_record(root: str) -> dict[str, object] | None:
    """Schema-2 result for this HEAD and this working tree, or None.

    A dirty edit, another commit, or a missing file is not this
    record. A matching record is returned whether or not the run passed.
    """
    record, reason = _matching_record(root)
    if reason in ("missing", "stale"):
        return None
    return record


def _failures_from_record(root: str, started: datetime) -> tuple[list[str], list[str]] | None:
    """Failed gate names and their logs, when the record is this invocation.

    The record is this run only when it is the current tree and ``finished``
    is after *started*. Anything else is a previous run, and the caller
    scrapes failure and timeout names from the text.
    """
    record = _current_record(root)
    if record is None:
        return None
    finished_raw = record.get("finished")
    if not isinstance(finished_raw, str):
        return None
    try:
        finished = datetime.fromisoformat(finished_raw)
    except ValueError:
        return None
    if finished.tzinfo is None or finished <= started:
        return None
    gates = record.get("gates")
    if not isinstance(gates, list):
        return None
    failed: list[str] = []
    logs: list[str] = []
    for gate in gates:
        if not isinstance(gate, dict) or gate.get("status") not in _RECORD_FAILURES:
            continue
        label = _gate_label(gate)
        if not label:
            continue
        log = gate.get("log")
        failed.append(label)
        logs.append(log if isinstance(log, str) else "")
    return failed, logs


def _failed_gate_names(stdout: str) -> list[str]:
    """Gate names ``gate`` reported as failing, from its own summary lines.

    ``gate`` writes ``gate: FAIL exit <n>  <command>  <duration>`` and
    ``gate: TIMEOUT after <d>  <command>  (killed, not failed)``. Fields are
    separated by two or more spaces, so a command containing single spaces
    survives the split intact.
    """
    failed: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("gate: FAIL"):
            rest = stripped.removeprefix("gate: FAIL").strip()
            fields = _FIELD_SPLIT.split(rest)
            # fields: ["exit <n>", "<command>", "<duration>", ...]
            failed.append(fields[1] if len(fields) > 1 else rest)
        elif stripped.startswith("gate: TIMEOUT"):
            rest = stripped.removeprefix("gate: TIMEOUT").strip()
            fields = _FIELD_SPLIT.split(rest)
            # fields: ["after <d>", "<command>", "(killed, not failed)", ...]
            if len(fields) > 1:
                failed.append(fields[1])
            elif rest:
                failed.append(rest)
    return failed


def _run_one(
    repo: Path, roots: list[str], *, e2e: bool = False, skip_fresh: bool = False
) -> GateResult:
    result = GateResult(repo=repo, label=repo_label(repo, roots))
    started = time.monotonic()
    try:
        names, root, left_out = _gate_listing(repo, e2e=e2e)
        result.e2e_left_out = left_out
        if not names:
            # Every gate was e2e and left out. The repo still counts as gated
            # for the gates that ran (none); it is not "no gate declared".
            result.status = "ok" if left_out else "none"
            return result
        result.gates = names
        # A previous full pass for this commit and this tree is still that pass.
        # ``partial`` is a named ``gate run``: exit 0 there is not the whole gate.
        # ``--e2e`` only changes the names a run that still happens receives.
        if skip_fresh:
            _, reason = _matching_record(root)
            if reason is None:
                result.status = "skipped"
                result.skip_reason = "fresh"
                return result
            result.gate_record_reason = reason
            if reason == "partial":
                result.record_partial = True
        # gate's own per-gate timeout would race this one; 0 disables it.
        started_at = datetime.now().astimezone()
        proc = run(
            ["gate", "--timeout", "0", "run", *names],
            repo,
            timeout=TIMEOUT_GATE,
        )
        if proc.returncode != 0:
            result.status = "fail"
            out = proc.stdout + proc.stderr
            recorded = _failures_from_record(root, started_at)
            if recorded is None:
                result.failed = _failed_gate_names(out)
            else:
                result.failed, result.logs = recorded
            result.detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
    except (OSError, ValueError) as exc:
        result.status = "error"
        result.detail = str(exc)
    finally:
        result.elapsed_s = time.monotonic() - started
    return result


def _print_report(results: list[GateResult], *, elapsed: float) -> None:
    broken = [r for r in results if r.status in ("fail", "error")]
    ran = [r for r in results if r.status not in ("none", "skipped")]
    if broken:
        print()
        for r in sorted(broken, key=lambda r: r.label):
            print(f"  FAIL {r.label}")
            for index, name in enumerate(r.failed):
                log = r.logs[index] if index < len(r.logs) else ""
                print(f"       {name}  {log}" if log else f"       {name}")
            if not r.failed and r.detail:
                print(f"       {r.detail.splitlines()[0][:160]}")
    slowest = sorted(ran, key=lambda r: -r.elapsed_s)[:5]
    print()
    print("Summary")
    print(f"  repos gated       {len(ran)}")
    print(f"  passing           {sum(1 for r in ran if r.status == 'ok')}")
    print(f"  failing           {len(broken)}")
    print(f"  skipped           {sum(1 for r in results if r.status == 'skipped')}")
    print(f"  no gate declared  {sum(1 for r in results if r.status == 'none')}")
    print(f"  e2e left out      {sum(r.e2e_left_out for r in results)}")
    print(f"  wall              {elapsed:.1f}s")
    if slowest:
        print()
        rendered = ", ".join(f"{r.label} {r.elapsed_s:.1f}s" for r in slowest)
        print(f"  slowest: {rendered}")


def build_gates_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops gates",
        description="Run each repo's own gate across the fleet and report failures.",
        allow_abbrev=False,
    )
    add_scan_arguments(parser)
    parser.add_argument(
        "--e2e",
        action="store_true",
        help="Also run e2e gates a default gate run skips.",
    )
    parser.add_argument(
        "--skip-fresh",
        action="store_true",
        help="Skip a repo whose schema-2 result matches this tree, exited 0, and is not partial.",
    )
    return parser


def _report_document(
    results: list[GateResult],
    *,
    roots: list[str],
    elapsed: float,
    excluded: int,
    exit_code: int,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "gates",
        "roots": roots,
        "elapsed_s": round(elapsed, 2),
        "excluded": excluded,
        "e2e_left_out": sum(r.e2e_left_out for r in results),
        "skipped": sum(1 for r in results if r.status == "skipped"),
        "record_partial": sum(1 for r in results if r.record_partial),
        "exit_code": exit_code,
        "repos": [r.to_json() for r in results],
    }


def main_gates(argv: list[str] | None = None) -> int:
    parser = build_gates_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)
    if not shutil.which("gate"):
        print("repo-ops gates: gate is not on PATH", file=sys.stderr)
        return 2
    roots = resolve_scan_roots(list(args.roots or DEFAULT_ROOTS), parser)
    if args.jobs is None:
        # Each worker runs a whole project gate, same sizing as apply.
        args.jobs = max(4, min(8, (os.cpu_count() or 4) // 4))
    patterns = merge_exclude_patterns(args.exclude_patterns, load_global_config().get("exclude"))

    run_dir = new_run_dir("gates")
    log_path, report_path = run_dir / "log", run_dir / "report.json"
    with tee_stdout_stderr(log_path):
        repos = sorted({Path(p) for p in discover_repos(roots).values()})
        kept, excluded = filter_repo_paths(repos, patterns, label_fn=lambda p: repo_label(p, roots))
        print(f"repo-ops gates: {len(kept)} repo(s), {args.jobs} worker(s)")
        if excluded:
            print(f"excluded {excluded} repo(s) by exclude patterns")
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = run_bounded_pool(
                pool,
                kept,
                lambda r: _run_one(r, roots, e2e=args.e2e, skip_fresh=args.skip_fresh),
                max_in_flight=args.jobs,
            )
        elapsed = time.monotonic() - started
        _print_report(results, elapsed=elapsed)
        exit_code = 1 if any(r.status in ("fail", "error") for r in results) else 0
        write_json_atomic(
            report_path,
            _report_document(
                results,
                roots=roots,
                elapsed=elapsed,
                excluded=excluded,
                exit_code=exit_code,
            ),
        )
    print(f"repo-ops: log {log_path}  report {report_path}")
    return exit_code
