"""Tests for CLI helpers and console entry point --help smoke."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from unittest.mock import patch

import pytest

from repo_ops._register import register_all
from repo_ops_core.cli import (
    build_upgrade_parser,
    positive_int,
)

_MAIN_HELP_TARGETS: list[tuple[str, str, list[str]]] = [
    ("repo_ops_upgrade.run", "main_plan", []),
    ("repo_ops_upgrade.run", "main_apply", []),
    ("repo_ops_dependabot.dependabot", "main", []),
]


def _call_main_help(module_name: str, func_name: str, prefix: list[str]) -> None:
    register_all()
    mod = importlib.import_module(module_name)
    fn: Callable[..., object] = getattr(mod, func_name)
    with pytest.raises(SystemExit) as exc_info:
        fn([*prefix, "--help"])
    assert exc_info.value.code == 0


def test_upgrade_parser_rejects_non_positive_jobs():
    parser = build_upgrade_parser(apply=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--jobs", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--jobs", "-1"])


def test_positive_int_rejects_zero_and_negative():
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("-3")
    assert positive_int("4") == 4


@pytest.mark.parametrize("module_name,func_name,prefix", _MAIN_HELP_TARGETS)
def test_entrypoint_main_help(module_name: str, func_name: str, prefix: list[str]) -> None:
    _call_main_help(module_name, func_name, prefix)


def test_repo_ops_dispatcher_subcommands_help(capsys) -> None:
    """Dispatcher delegates --help to full subcommand parsers (return 0, no exit)."""
    from repo_ops import cli

    for sub in ("plan", "apply", "dependabot"):
        assert cli.main([sub, "--help"]) == 0
        out = capsys.readouterr().out
        assert "usage: repo-ops" in out
        assert sub in out


def test_dispatcher_forwards_empty_list_for_bare_subcommand() -> None:
    """Regression: a bare subcommand (e.g. ``repo-ops plan``) must forward an
    empty list to the handler, not ``None``. ``None`` makes the handler's
    argparse fall back to ``sys.argv[1:]`` and re-consume the subcommand token
    ("plan") as a positional root, defeating the DEFAULT_ROOTS default.
    """
    from repo_ops import cli

    captured: dict[str, list[str] | None] = {}

    def _recorder(argv: list[str] | None) -> int:
        captured["argv"] = argv
        return 0

    with patch.dict(cli._HANDLERS, {"plan": _recorder}):
        assert cli.main(["plan"]) == 0
        assert captured["argv"] == []
        assert cli.main(["plan", "/some/root"]) == 0
        assert captured["argv"] == ["/some/root"]


def test_cli_version_flag_short_circuits(capsys) -> None:
    """Repo-ops --version prints the installed version and exits 0."""
    from repo_ops import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("repo-ops ")
    # _resolve_version pulls importlib.metadata.version("repo-ops").
    assert cli._resolve_version() in out


def test_cli_handler_dicts_have_matching_keys() -> None:
    """Dispatcher routing dicts must stay in sync."""
    from repo_ops import cli

    handler_keys = set(cli._HANDLERS)
    builder_keys = set(cli._HELP_BUILDERS)
    help_keys = set(cli._SUBCOMMAND_HELP)
    assert handler_keys == builder_keys == help_keys


def test_upgrade_parser_exclude_repeatable():
    """--exclude is repeatable and stores a list."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--exclude", "foo", "--exclude", "bar"])
    assert args.exclude_patterns == ["foo", "bar"]
    args_none = parser.parse_args([])
    assert args_none.exclude_patterns is None


def test_apply_help_matches_wired_flags_and_defaults() -> None:
    """apply --help must name the real jobs default, exclude match, and apply-only flags."""
    apply_help = build_upgrade_parser(apply=True).format_help()
    plan_help = build_upgrade_parser(apply=False).format_help()
    assert "max(4, min(8, cpus/4))" in apply_help
    assert "label or path" in apply_help
    assert "Skip verification (implies --no-commit)." in apply_help
    assert "--no-commit" in apply_help
    assert "--filter" in apply_help
    assert "--no-commit" not in plan_help
    assert "--no-verify" not in plan_help
    assert "Skip verification (implies --no-commit)." not in plan_help
