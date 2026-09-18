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
