"""Tests for CLI helpers and console entry point --help smoke."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable

import pytest

from repo_ops.core.cli import parse_iso8601

# (module, callable name, argv prefix before --help)
_MAIN_HELP_TARGETS: list[tuple[str, str, list[str]]] = [
    ("repo_ops.inventory.scan", "main", []),
    ("repo_ops.upgrade.run", "main_plan", []),
    ("repo_ops.upgrade.run", "main_apply", []),
    ("repo_ops.pushed.audit", "main", []),
    ("repo_ops.commands.dispatch", "main", ["plan"]),
]


def _call_main_help(module_name: str, func_name: str, prefix: list[str]) -> None:
    mod = importlib.import_module(module_name)
    fn: Callable[..., object] = getattr(mod, func_name)
    with pytest.raises(SystemExit) as exc_info:
        fn([*prefix, "--help"])
    assert exc_info.value.code == 0


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


@pytest.mark.parametrize("module_name,func_name,prefix", _MAIN_HELP_TARGETS)
def test_entrypoint_main_help(
    module_name: str, func_name: str, prefix: list[str]
) -> None:
    _call_main_help(module_name, func_name, prefix)


def test_repo_ops_dispatcher_subcommands_help() -> None:
    for sub in ("inventory", "plan", "apply", "pushed"):
        _call_main_help("repo_ops.commands.dispatch", "main", [sub])
