"""ECO_FILTER_CHOICES and --log-level / -v."""

from __future__ import annotations

import logging

import pytest

from repo_ops_core.cli import (
    ECO_FILTER_CHOICES,
    build_upgrade_parser,
)
from repo_ops_core.process import configure_logging

# ---------------------------------------------------------------------------
# ECO_FILTER_CHOICES coverage
# ---------------------------------------------------------------------------


def test_eco_filter_choices_includes_precommit_and_actions():
    """Precommit and actions must appear in ECO_FILTER_CHOICES."""
    assert "precommit" in ECO_FILTER_CHOICES
    assert "actions" in ECO_FILTER_CHOICES


@pytest.mark.parametrize(
    ("parser_builder", "filter_value"),
    [
        (lambda: build_upgrade_parser(apply=False), "precommit"),
        (lambda: build_upgrade_parser(apply=False), "actions"),
    ],
    ids=[
        "upgrade-precommit",
        "upgrade-actions",
    ],
)
def test_filter_flag_accepted(parser_builder, filter_value):
    """--filter precommit/actions must parse without error on plan/apply."""
    parser = parser_builder()
    args = parser.parse_args(["--filter", filter_value])
    assert args.eco_filter == filter_value


# ---------------------------------------------------------------------------
# --log-level / -v
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parser_builder", "level"),
    [
        (lambda: build_upgrade_parser(apply=False), "warning"),
    ],
    ids=["upgrade"],
)
def test_log_level_argument_registered(parser_builder, level):
    """The plan/apply parser accepts --log-level."""
    parser = parser_builder()
    args = parser.parse_args(["--log-level", level])
    assert args.log_level == level


def test_log_level_choices():
    """--log-level accepts debug/info/warning/error only."""
    parser = build_upgrade_parser(apply=False)
    for level in ("debug", "info", "warning", "error"):
        args = parser.parse_args(["--log-level", level])
        assert args.log_level == level
    with pytest.raises(SystemExit):
        parser.parse_args(["--log-level", "trace"])


def test_configure_logging_sets_root_level():
    """configure_logging sets the stdlib root logger to the given level."""
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
    configure_logging("info")
    assert logging.getLogger().level == logging.INFO
    # Restore
    configure_logging("warning")


def test_configure_logging_debug_implies_verbose(monkeypatch):
    """configure_logging('debug') sets VERBOSE flag for subprocess echo."""
    import repo_ops_core.process as proc_mod

    monkeypatch.setattr(proc_mod, "VERBOSE", False)
    configure_logging("debug")
    assert proc_mod.VERBOSE is True
    # Restore
    monkeypatch.setattr(proc_mod, "VERBOSE", False)


def test_configure_logging_noop_on_none():
    """configure_logging(None) must not touch root logger level."""
    before = logging.getLogger().level
    configure_logging(None)
    assert logging.getLogger().level == before


def test_verbose_flag_implies_debug_when_no_log_level():
    """wire_verbose with --verbose and no --log-level must reach debug level."""
    import repo_ops_core.process as proc_mod
    from repo_ops_core.cli import wire_verbose

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--verbose"])
    old_verbose = proc_mod.VERBOSE
    try:
        wire_verbose(args)
        assert proc_mod.VERBOSE is True
        assert logging.getLogger().level == logging.DEBUG
    finally:
        proc_mod.VERBOSE = old_verbose


def test_verbose_and_log_level_combined():
    """--verbose --log-level warning: subprocess echo on, stdlib at WARNING."""
    import repo_ops_core.process as proc_mod
    from repo_ops_core.cli import wire_verbose

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--verbose", "--log-level", "warning"])
    old_verbose = proc_mod.VERBOSE
    try:
        wire_verbose(args)
        assert proc_mod.VERBOSE is True
        assert logging.getLogger().level == logging.WARNING
    finally:
        proc_mod.VERBOSE = old_verbose
