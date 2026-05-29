"""Tests for CLI helpers and console entry point --help smoke."""

from __future__ import annotations

import argparse
import shutil
import subprocess

import pytest

from repo_ops.core.cli import parse_iso8601

_ENTRY_POINTS = (
    ("repos-inventory", []),
    ("repos-upgrade-plan", []),
    ("repos-upgrade-apply", []),
    ("repos-pushed", []),
    ("repo-ops", ["plan"]),
)


def test_parse_iso8601_accepts_z_and_returns_utc():
    dt = parse_iso8601("2026-05-29T14:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026
    assert dt.hour == 14


def test_parse_iso8601_rejects_naive():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_iso8601("2026-05-29T14:00:00")


def test_parse_iso8601_invalid_raises_argparse_error():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_iso8601("not-a-date")


@pytest.mark.parametrize("script,extra_args", _ENTRY_POINTS)
def test_console_scripts_help(script: str, extra_args: list[str]) -> None:
    exe = shutil.which(script)
    if exe is None:
        pytest.skip(f"{script} not on PATH (install package editable first)")
    proc = subprocess.run(
        [exe, *extra_args, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert script.split("-", 1)[-1] in proc.stdout or "usage:" in proc.stdout.lower()


def test_repo_ops_dispatcher_subcommands_help() -> None:
    exe = shutil.which("repo-ops")
    if exe is None:
        pytest.skip("repo-ops not on PATH")
    for sub in ("inventory", "plan", "apply", "pushed"):
        proc = subprocess.run(
            [exe, sub, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (sub, proc.stderr)
