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
import sys
import time
from dataclasses import dataclass, field
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


@dataclass
class GateResult:
    repo: Path
    label: str
    status: str = "ok"  # ok | fail | none | error
    gates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    detail: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "repo": str(self.repo),
            "label": self.label,
            "status": self.status,
            "gates": self.gates,
            "failed": self.failed,
            "elapsed_s": round(self.elapsed_s, 2),
            "detail": self.detail,
        }


def _gate_names(repo: Path) -> list[str]:
    """Gate names *repo* declares, via ``gate --json``.

    ``gate --json`` executes nothing and exits 0 even where it detects nothing,
    so it doubles as the availability probe and the gate set.
    """
    proc = run(["gate", "--json"], repo, timeout=TIMEOUT_LIST)
    if proc.returncode != 0:
        return []
    try:
        return [g["name"] for g in json.loads(proc.stdout).get("gates") or []]
    except (ValueError, AttributeError, TypeError, KeyError):
        return []


def _failed_gate_names(stdout: str) -> list[str]:
    """Gate names ``gate`` reported as failing, from its own summary lines.

    ``gate`` writes ``gate: FAIL exit <n>  <command>  <duration>`` and separates
    those fields by two or more spaces, so a command containing single spaces
    survives the split intact.
    """
    failed: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("gate: FAIL"):
            continue
        rest = stripped.removeprefix("gate: FAIL").strip()
        fields = _FIELD_SPLIT.split(rest)
        # fields: ["exit <n>", "<command>", "<duration>", ...]
        failed.append(fields[1] if len(fields) > 1 else rest)
    return failed


def _run_one(repo: Path, roots: list[str]) -> GateResult:
    result = GateResult(repo=repo, label=repo_label(repo, roots))
    started = time.monotonic()
    try:
        names = _gate_names(repo)
        if not names:
            result.status = "none"
            return result
        result.gates = names
        # gate's own per-gate timeout would race this one; 0 disables it.
        proc = run(
            ["gate", "--timeout", "0", "run", *names],
            repo,
            timeout=TIMEOUT_GATE,
        )
        if proc.returncode != 0:
            result.status = "fail"
            out = proc.stdout + proc.stderr
            result.failed = _failed_gate_names(out)
            result.detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
    except (OSError, ValueError) as exc:
        result.status = "error"
        result.detail = str(exc)
    finally:
        result.elapsed_s = time.monotonic() - started
    return result


def _print_report(results: list[GateResult], *, elapsed: float) -> None:
    broken = [r for r in results if r.status in ("fail", "error")]
    ran = [r for r in results if r.status != "none"]
    if broken:
        print()
        for r in sorted(broken, key=lambda r: r.label):
            names = ", ".join(r.failed) if r.failed else r.detail.splitlines()[:1]
            print(f"  FAIL {r.label}")
            for name in r.failed or []:
                print(f"       {name}")
            if not r.failed and r.detail:
                print(f"       {r.detail.splitlines()[0][:160]}")
            del names
    slowest = sorted(ran, key=lambda r: -r.elapsed_s)[:5]
    print()
    print("Summary")
    print(f"  repos gated       {len(ran)}")
    print(f"  passing           {sum(1 for r in ran if r.status == 'ok')}")
    print(f"  failing           {len(broken)}")
    print(f"  no gate declared  {sum(1 for r in results if r.status == 'none')}")
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
    return parser


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
                pool, kept, lambda r: _run_one(r, roots), max_in_flight=args.jobs
            )
        elapsed = time.monotonic() - started
        _print_report(results, elapsed=elapsed)
        exit_code = 1 if any(r.status in ("fail", "error") for r in results) else 0
        write_json_atomic(
            report_path,
            {
                "schema_version": 1,
                "mode": "gates",
                "roots": roots,
                "elapsed_s": round(elapsed, 2),
                "excluded": excluded,
                "exit_code": exit_code,
                "repos": [r.to_json() for r in results],
            },
        )
    print(f"repo-ops: log {log_path}  report {report_path}")
    return exit_code
