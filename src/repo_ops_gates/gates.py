"""Fleet-wide gate run: every repo's declared gate, reported together.

``apply`` verifies only what it changed, so a repo nobody has touched can sit
broken indefinitely with nothing saying so. This walks the same roots and runs
each project's own gate, which is the check its CI would run.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
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
from repo_ops_core.git import repo_label, run_git
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
_RECORD_SCHEMA = 2


@dataclass
class GateResult:
    repo: Path
    label: str
    status: str = "ok"  # ok | fail | none | error
    gates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    detail: str = ""
    e2e_left_out: int = 0

    def to_json(self) -> dict[str, object]:
        return {
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


def _state_dir() -> Path:
    """Directory gate uses for its result records.

    A relative ``XDG_STATE_HOME`` is ignored, matching gate: the XDG spec says
    a reader then assumes ``~/.local/state``.
    """
    raw = os.environ.get("XDG_STATE_HOME", "")
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return Path.home() / ".local" / "state"


def _record_path(root: str) -> Path:
    """Path of gate's result record for *root*.

    Gate hashes the symlink-resolved project root and writes
    ``$XDG_STATE_HOME/gate/results/<sha256>.json``.
    """
    canonical = os.path.realpath(root)
    digest = hashlib.sha256(os.fsencode(canonical)).hexdigest()
    return _state_dir() / "gate" / "results" / f"{digest}.json"


def _checkout_head(repo: Path) -> str | None:
    proc = run_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip()
    return head or None


def _git_bytes(repo: Path, *args: str) -> bytes | None:
    """Stdout of a git read, or None when git fails.

    The flags match gate's result-record hash: no fsmonitor program, no color,
    no optional locks, so the bytes do not depend on which of those is set.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.fsmonitor=", "-c", "color.ui=false", *args],
            check=False,
            capture_output=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _untracked_paths(status: bytes) -> list[bytes]:
    """``??`` paths from porcelain v1 ``-z``. A rename or copy is two paths."""
    paths: list[bytes] = []
    while status:
        end = status.find(b"\0")
        if end < 0:
            break
        rec = status[:end]
        status = status[end + 1 :]
        if len(rec) < 4 or rec[2:3] != b" ":
            continue
        xy, path = rec[:2], rec[3:]
        if xy[0:1] in (b"R", b"C") or xy[1:2] in (b"R", b"C"):
            nxt = status.find(b"\0")
            if nxt < 0:
                break
            status = status[nxt + 1 :]
        if xy == b"??":
            paths.append(path)
    return paths


def _untracked_files(repo: Path, status: bytes) -> list[str] | None:
    """Untracked files, directories expanded, in the order status printed them."""
    files: list[bytes] = []
    for path in _untracked_paths(status):
        if path.endswith(b"/"):
            listed = _git_bytes(
                repo,
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
                "--",
                os.fsdecode(path[:-1]),
            )
            if listed is None:
                return None
            files.extend(part for part in listed.split(b"\0") if part)
            continue
        files.append(path)
    return [os.fsdecode(path) for path in files]


def _hash_objects(repo: Path, files: list[str]) -> bytes | None:
    """Concatenated ``git hash-object`` stdout. Never passed ``-w``."""
    if not files:
        return b""
    out = bytearray()
    batch = 50
    for start in range(0, len(files), batch):
        hashed = _git_bytes(repo, "hash-object", "--", *files[start : start + batch])
        if hashed is None:
            return None
        out.extend(hashed)
    return bytes(out)


def _status_hash(root: Path) -> str | None:
    """SHA-256 gate writes as ``status`` on a schema-2 result record.

    The bytes are concatenated, in order: porcelain v1 ``-z`` status,
    ``git diff HEAD``, and ``git hash-object`` (no ``-w``) of each untracked
    file. A clean tree is empty status plus an empty diff.
    """
    status = _git_bytes(root, "status", "--porcelain=v1", "-z")
    diff = _git_bytes(root, "diff", "HEAD")
    if status is None or diff is None:
        return None
    files = _untracked_files(root, status)
    if files is None:
        return None
    blobs = _hash_objects(root, files)
    if blobs is None:
        return None
    return hashlib.sha256(status + diff + blobs).hexdigest()


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


def _failures_from_record(
    repo: Path, root: str, started: datetime
) -> tuple[list[str], list[str]] | None:
    """Failed gate names and their logs, when the record is this invocation.

    The record is this run only when its schema is 2, its ``head`` is the
    checkout's HEAD, its ``status`` is this working tree, and ``finished`` is
    after *started*. Anything else is a previous run, and the caller scrapes
    failure and timeout names from the text.
    """
    head = _checkout_head(repo)
    if head is None or not root:
        return None
    try:
        record = json.loads(_record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = _status_hash(Path(root))
    if (
        not isinstance(record, dict)
        or record.get("schema") != _RECORD_SCHEMA
        or record.get("head") != head
        or not isinstance(record.get("status"), str)
        or status is None
        or record.get("status") != status
    ):
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


def _run_one(repo: Path, roots: list[str], *, e2e: bool = False) -> GateResult:
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
            recorded = _failures_from_record(repo, root, started_at)
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
    ran = [r for r in results if r.status != "none"]
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
                pool, kept, lambda r: _run_one(r, roots, e2e=args.e2e), max_in_flight=args.jobs
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
                "e2e_left_out": sum(r.e2e_left_out for r in results),
                "exit_code": exit_code,
                "repos": [r.to_json() for r in results],
            },
        )
    print(f"repo-ops: log {log_path}  report {report_path}")
    return exit_code
