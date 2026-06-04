"""Tests for CLI helpers and console entry point --help smoke."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from unittest.mock import patch

import pytest
from repo_ops._register import register_all
from repo_ops_core.cli import (
    ECO_FILTER_HELP,
    MANAGER_FILTER_CHOICES,
    add_eco_filter_argument,
    build_upgrade_parser,
    parse_iso8601,
    positive_int,
)

_MAIN_HELP_TARGETS: list[tuple[str, str, list[str]]] = [
    ("repo_ops_inventory.scan", "main", []),
    ("repo_ops_upgrade.run", "main_plan", []),
    ("repo_ops_upgrade.run", "main_apply", []),
    ("repo_ops_pushed.audit", "main", []),
    ("repo_ops.cli", "main", ["plan"]),
    ("repo_ops.install_skill", "main", []),
]


def _call_main_help(module_name: str, func_name: str, prefix: list[str]) -> None:
    register_all()
    mod = importlib.import_module(module_name)
    fn: Callable[..., object] = getattr(mod, func_name)
    with pytest.raises(SystemExit) as exc_info:
        fn([*prefix, "--help"])
    assert exc_info.value.code == 0


def test_eco_filter_help_on_inventory_and_upgrade_parsers():
    bare = argparse.ArgumentParser()
    add_eco_filter_argument(bare)
    upgrade = build_upgrade_parser(apply=False)
    for parser in (bare, upgrade):
        action = next(a for a in parser._actions if a.dest == "eco_filter")
        assert action.help == ECO_FILTER_HELP


def test_upgrade_parser_rejects_non_positive_jobs():
    parser = build_upgrade_parser(apply=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--jobs", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--jobs", "-1"])


def test_upgrade_parser_rejects_non_positive_top():
    """Regression #88: --top accepted negative integers, corrupting change list slices."""
    parser = build_upgrade_parser(apply=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--top", "-1"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--top", "0"])
    args = parser.parse_args(["--top", "5"])
    assert args.top == 5


def test_positive_int_rejects_zero_and_negative():
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("-3")
    assert positive_int("4") == 4


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
    for sub in ("inventory", "plan", "apply", "pushed", "install-skill"):
        _call_main_help("repo_ops.cli", "main", [sub])


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
    """#104: repo-ops --version prints the installed version and exits 0."""
    from repo_ops import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("repo-ops ")
    # _resolve_version pulls importlib.metadata.version("repo-ops").
    assert cli._resolve_version() in out


def test_manager_filter_choices_match_registry() -> None:
    """#104: --manager choices stay in sync with the registry's CHECKERS keys.

    The choices are a static tuple in core to avoid a core->upgrade circular
    import; this guard fails loudly if a new manager is registered but the
    filter tuple is not updated.
    """
    register_all()
    from repo_ops_upgrade.registry import CHECKERS

    assert set(MANAGER_FILTER_CHOICES) == set(CHECKERS.keys())


def test_upgrade_parser_accepts_104_and_102_flags() -> None:
    """#104/#102: plan parser accepts --manager, --latest, --go-upgrade-mode."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(
        ["--manager", "npm", "--latest", "--go-upgrade-mode", "explicit"]
    )
    assert args.manager_filter == "npm"
    assert args.latest is True
    assert args.go_upgrade_mode == "explicit"
