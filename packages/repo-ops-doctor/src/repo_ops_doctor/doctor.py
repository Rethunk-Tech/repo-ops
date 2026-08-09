#!/usr/bin/env python3
"""Toolchain/auth preflight: probe the binaries repo-ops shells out to.

Read-only, no scan roots. Resolves each known binary via ``shutil.which``,
capturing its resolved path and a best-effort version string (typically the
first line of ``<tool> --version``, with per-tool overrides — ``go version``
takes no dashes and ``gofmt`` has no version flag at all, so its probe is
skipped; timeout-guarded and tolerant of tools that print to stderr or exit
non-zero). ``gh`` additionally gets a ``gh auth status`` check
so agents can tell "not installed" apart from "installed but unauthenticated"
before a ``dependabot``/``pr``-mode run fails partway through.

Console entry point: ``repo-ops doctor`` (see ``--help`` for flags).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from repo_ops_core.cli import (
    add_json_argument,
    add_json_out_argument,
    add_verbose_argument,
    wire_verbose,
)
from repo_ops_core.filesystem import write_json_atomic
from repo_ops_core.table import column_width, format_cell, join_row

_VERSION_TIMEOUT = 10.0
_AUTH_TIMEOUT = 15.0


@dataclass(frozen=True)
class BinarySpec:
    """One binary repo-ops probes for, grouped by the ecosystem it serves."""

    name: str
    ecosystem: str
    version_args: tuple[str, ...] = ("--version",)


# Static v1 table. Ecosystem labels are informational groupings for the human
# report, not a strict match to repo_ops_core.cli.ECO_FILTER_CHOICES.
#
# Sourced by grepping shutil.which() call sites across packages/ (git, gh,
# uv, pip-compile, poetry, pipenv, pdm, deno, cargo, actionlint, govulncheck,
# gofmt, go) plus binaries repo-ops invokes directly without a which() gate
# (npm/pnpm/yarn/bun via repo_ops_core.process.run, corepack via
# repo_ops_js.workspaces.corepack_prepare, pip-audit via
# ``uv run pip-audit --version`` in repo_ops_audit.audit).
# cargo-audit is a cargo subcommand (``cargo audit --version``), not a
# standalone binary — see ``_probe_cargo_audit``.
_BINARIES: tuple[BinarySpec, ...] = (
    BinarySpec("git", "vcs"),
    BinarySpec("gh", "vcs"),
    BinarySpec("uv", "python"),
    BinarySpec("pyrefly", "python"),
    BinarySpec("hatch", "python"),
    BinarySpec("pip-compile", "python"),
    BinarySpec("poetry", "python"),
    BinarySpec("pipenv", "python"),
    BinarySpec("pdm", "python"),
    BinarySpec("pip-audit", "python"),
    BinarySpec("pre-commit", "precommit"),
    BinarySpec("npm", "node"),
    BinarySpec("pnpm", "node"),
    BinarySpec("yarn", "node"),
    BinarySpec("bun", "node"),
    BinarySpec("corepack", "node"),
    BinarySpec("deno", "deno"),
    # go's version flag has no leading dashes ("go version", not "go --version").
    BinarySpec("go", "go", version_args=("version",)),
    # gofmt has no version flag at all (it shares the go toolchain's version, and
    # with zero args it blocks reading stdin) — version_args=() skips the probe
    # entirely rather than risk a hang or a misleading "flag not defined" line.
    BinarySpec("gofmt", "go", version_args=()),
    BinarySpec("govulncheck", "go"),
    BinarySpec("cargo", "rust"),
    # cargo-audit: probed via ``cargo audit --version`` in probe_binary.
    BinarySpec("cargo-audit", "rust", version_args=()),
    BinarySpec("actionlint", "actions"),
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ecosystem: str
    found: bool
    path: str | None
    version: str | None


@dataclass(frozen=True)
class GhAuthStatus:
    checked: bool
    authenticated: bool | None
    detail: str | None


def _first_line(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    return text.splitlines()[0].strip()


def _probe_version(path: str, version_args: tuple[str, ...]) -> str | None:
    """Best-effort ``<path> <version_args>`` first-line output, or None.

    Empty ``version_args`` skips the probe entirely: some tools (e.g. gofmt)
    have no version flag and block reading stdin when invoked bare, so there
    is no safe invocation to try. ``stdin=DEVNULL`` guards the remaining
    invocations against the same hang if a tool ever falls back to reading
    stdin on an unrecognised flag.
    """
    if not version_args:
        return None
    try:
        proc = subprocess.run(
            [path, *version_args],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _first_line(proc.stdout) or _first_line(proc.stderr)


def _probe_cargo_audit() -> ProbeResult:
    """Probe ``cargo audit`` subcommand availability (cargo-audit plugin)."""
    cargo_path = shutil.which("cargo")
    if cargo_path is None:
        return ProbeResult("cargo-audit", "rust", False, None, None)
    try:
        proc = subprocess.run(
            [cargo_path, "audit", "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ProbeResult("cargo-audit", "rust", False, cargo_path, None)
    if proc.returncode != 0:
        return ProbeResult("cargo-audit", "rust", False, cargo_path, None)
    version = _first_line(proc.stdout) or _first_line(proc.stderr)
    return ProbeResult("cargo-audit", "rust", True, cargo_path, version)


def probe_binary(spec: BinarySpec) -> ProbeResult:
    if spec.name == "cargo-audit":
        return _probe_cargo_audit()
    path = shutil.which(spec.name)
    if path is None:
        return ProbeResult(spec.name, spec.ecosystem, False, None, None)
    version = _probe_version(path, spec.version_args)
    return ProbeResult(spec.name, spec.ecosystem, True, path, version)


def probe_all(binaries: tuple[BinarySpec, ...] = _BINARIES) -> list[ProbeResult]:
    return [probe_binary(spec) for spec in binaries]


def probe_gh_auth(gh_path: str | None) -> GhAuthStatus:
    """Run ``gh auth status`` when ``gh`` is on PATH; never raises.

    ``authenticated`` is None when gh is missing or the check itself failed
    to execute (timeout/OSError) — distinct from a clean "not logged in"
    (authenticated=False, exit 1).
    """
    if gh_path is None:
        return GhAuthStatus(checked=False, authenticated=None, detail=None)
    try:
        proc = subprocess.run(
            [gh_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=_AUTH_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GhAuthStatus(checked=True, authenticated=None, detail=str(exc))
    detail = _first_line(proc.stdout) or _first_line(proc.stderr)
    return GhAuthStatus(checked=True, authenticated=proc.returncode == 0, detail=detail)


def _build_payload(results: list[ProbeResult], gh_auth: GhAuthStatus) -> dict:
    missing = [r.name for r in results if not r.found]
    return {
        "schema_version": 1,
        "binaries": [
            {
                "name": r.name,
                "ecosystem": r.ecosystem,
                "found": r.found,
                "path": r.path,
                "version": r.version,
            }
            for r in results
        ],
        "gh_auth": {
            "checked": gh_auth.checked,
            "authenticated": gh_auth.authenticated,
            "detail": gh_auth.detail,
        },
        "missing": missing,
        "found_count": len(results) - len(missing),
        "total_count": len(results),
    }


def print_report(results: list[ProbeResult], gh_auth: GhAuthStatus) -> None:
    found = [r for r in results if r.found]
    missing = [r for r in results if not r.found]

    print(f"Toolchain preflight: {len(found)}/{len(results)} binaries found")
    print()

    names = [r.name for r in results]
    ecosystems = [r.ecosystem for r in results]
    statuses = ["found" if r.found else "missing" for r in results]
    versions = [r.version or "" for r in results]
    paths = [r.path or "" for r in results]

    name_w = column_width(names, "binary")
    eco_w = column_width(ecosystems, "ecosystem")
    status_w = column_width(statuses, "status")
    version_w = column_width(versions, "version", cap=40)
    path_w = column_width(paths, "path", cap=60)

    header = join_row(
        [
            format_cell("binary", name_w),
            format_cell("ecosystem", eco_w),
            format_cell("status", status_w),
            format_cell("version", version_w),
            format_cell("path", path_w),
        ]
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            join_row(
                [
                    format_cell(r.name, name_w),
                    format_cell(r.ecosystem, eco_w),
                    format_cell("found" if r.found else "missing", status_w),
                    format_cell(r.version or "", version_w),
                    format_cell(r.path or "", path_w),
                ]
            )
        )

    print()
    if gh_auth.checked:
        if gh_auth.authenticated is True:
            print(
                "gh auth: authenticated"
                + (f" ({gh_auth.detail})" if gh_auth.detail else "")
            )
        elif gh_auth.authenticated is False:
            print(
                "gh auth: NOT authenticated"
                + (f" ({gh_auth.detail})" if gh_auth.detail else "")
            )
        else:
            print(f"gh auth: check failed ({gh_auth.detail or 'unknown error'})")
    else:
        print("gh auth: skipped (gh not found)")

    if missing:
        print()
        print(f"Missing: {', '.join(r.name for r in missing)}")


def build_doctor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops doctor",
        description=(
            "Toolchain/auth preflight: probe known binaries repo-ops shells out to "
            "(found/missing, resolved path, version) and check gh auth status. "
            "Read-only; no scan roots."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any probed binary is missing (default: exit 0 regardless).",
    )
    add_json_argument(parser, dest="json_out")
    add_json_out_argument(parser)
    add_verbose_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_doctor_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)
    if getattr(args, "json_report_path", None) and not args.json_out:
        parser.error("--json-out requires --json")

    results = probe_all()
    gh_result = next((r for r in results if r.name == "gh"), None)
    gh_auth = probe_gh_auth(gh_result.path if gh_result else None)

    if args.json_out:
        payload = _build_payload(results, gh_auth)
        json_report_path = getattr(args, "json_report_path", None)
        if json_report_path:
            written = write_json_atomic(
                Path(json_report_path), payload, compact=args.compact
            )
            print(written)
        else:
            print(json.dumps(payload, indent=None if args.compact else 2))
    else:
        print_report(results, gh_auth)

    if args.strict and any(not r.found for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
