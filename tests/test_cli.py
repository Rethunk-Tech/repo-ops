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
    ("repo_ops.install_skill", "main", []),
    ("repo_ops_audit.audit", "main", []),
    ("repo_ops_dependabot.dependabot", "main", []),
    ("repo_ops_lockfiles.sbom", "main", []),
    ("repo_ops_lockfiles.license_policy", "main", []),
    ("repo_ops_doctor.doctor", "main", []),
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
    """--top accepted negative integers, corrupting change list slices."""
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


# test_parse_iso8601_rejects_invalid in tests/test_pushed.py already covers the
# invalid-string case for this same parse_iso8601.


@pytest.mark.parametrize("module_name,func_name,prefix", _MAIN_HELP_TARGETS)
def test_entrypoint_main_help(module_name: str, func_name: str, prefix: list[str]) -> None:
    _call_main_help(module_name, func_name, prefix)


def test_repo_ops_dispatcher_subcommands_help(capsys) -> None:
    """Dispatcher delegates --help to full subcommand parsers (return 0, no exit)."""
    from repo_ops import cli

    for sub in (
        "inventory",
        "plan",
        "apply",
        "pushed",
        "install-skill",
        "audit",
        "dependabot",
        "sbom",
        "license",
        "doctor",
    ):
        assert cli.main([sub, "--help"]) == 0
        out = capsys.readouterr().out
        assert "usage: repo-ops" in out
        assert sub in out


def test_dispatcher_apply_help_lists_conservative_flag(capsys) -> None:
    """Regression: dispatcher --help must expose full flags, not only -h."""
    from repo_ops import cli

    assert cli.main(["apply", "--help"]) == 0
    out = capsys.readouterr().out
    # The sweep is on by default and --conservative is its single opt-out;
    # there are no per-manager targeted-bump or refresh-sha flags.
    assert "--conservative" in out
    assert "full sweep" in out
    assert "cargo test" in out
    assert "REPO_OPS_COMMIT_AGENT_MODEL" in out
    assert "composer-2.5" in out
    assert "root_dir" in out


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


def test_manager_filter_choices_match_registry() -> None:
    """--manager choices stay in sync with the registry's CHECKERS keys.

    The choices are a static tuple in core to avoid a core->upgrade circular
    import; this guard fails loudly if a new manager is registered but the
    filter tuple is not updated.
    """
    register_all()
    from repo_ops_upgrade.registry import CHECKERS

    assert set(MANAGER_FILTER_CHOICES) == set(CHECKERS.keys())


def test_upgrade_parser_accepts_104_and_102_flags() -> None:
    """Plan parser accepts policy and scope flags."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(
        [
            "--manager",
            "npm",
            "--conservative",
            "--go-upgrade-mode",
            "explicit",
        ]
    )
    assert args.manager_filter == "npm"
    assert args.conservative is True
    assert args.go_upgrade_mode == "explicit"


def test_upgrade_parser_accepts_actions_upgrade_mode() -> None:
    """Plan parser accepts --actions-upgrade-mode; defaults to None (umbrella)."""
    parser = build_upgrade_parser(apply=False)
    assert parser.parse_args([]).actions_upgrade_mode is None
    args = parser.parse_args(["--actions-upgrade-mode", "latest"])
    assert args.actions_upgrade_mode == "latest"


def test_upgrade_parser_actions_lint_level_choices():
    """--actions-lint-level accepts block/warn/off."""
    parser = build_upgrade_parser(apply=False)
    for level in ("block", "warn", "off"):
        args = parser.parse_args(["--actions-lint-level", level])
        assert args.actions_lint_level == level
    with pytest.raises(SystemExit):
        parser.parse_args(["--actions-lint-level", "invalid"])


def test_upgrade_parser_exclude_repeatable():
    """--exclude is repeatable and stores a list."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--exclude", "foo", "--exclude", "bar"])
    assert args.exclude_patterns == ["foo", "bar"]
    args_none = parser.parse_args([])
    assert args_none.exclude_patterns is None


def test_upgrade_parser_conservative_replaces_targeted_bump_flags() -> None:
    """The 16 --latest/--full/per-manager-targeted-bump/refresh-sha flags
    collapsed into one --conservative opt-out; the sweep is on by default and
    none of those argparse options exist anymore."""
    parser = build_upgrade_parser(apply=False)
    assert parser.parse_args([]).conservative is False
    assert parser.parse_args(["--conservative"]).conservative is True
    for removed_flag in (
        "--latest",
        "--full",
        "--yarn-targeted-bump",
        "--npm-targeted-bump",
        "--pnpm-targeted-bump",
        "--bun-targeted-bump",
        "--deno-targeted-bump",
        "--rust-targeted-bump",
        "--poetry-targeted-bump",
        "--pdm-targeted-bump",
        "--pipenv-targeted-bump",
        "--pip-tools-targeted-bump",
        "--actions-refresh-sha",
        "--actions-pin-sha",
        "--precommit-refresh-sha",
        "--submodule-refresh-sha",
        "--container-update-latest",
        "--container-refresh-sha",
    ):
        with pytest.raises(SystemExit):
            parser.parse_args([removed_flag])
