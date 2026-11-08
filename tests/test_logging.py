"""configure_logging and -v."""

from __future__ import annotations

import logging

import repo_ops_core.process as proc_mod


def test_configure_logging_sets_root_level():
    """configure_logging sets the stdlib root logger to the given level."""
    proc_mod.configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
    proc_mod.configure_logging("info")
    assert logging.getLogger().level == logging.INFO
    # Restore
    proc_mod.configure_logging("warning")


def test_configure_logging_debug_implies_verbose(monkeypatch):
    """configure_logging('debug') sets VERBOSE flag for subprocess echo."""
    monkeypatch.setattr(proc_mod, "VERBOSE", False)
    proc_mod.configure_logging("debug")
    assert proc_mod.VERBOSE is True
    # Restore
    monkeypatch.setattr(proc_mod, "VERBOSE", False)


def test_configure_logging_noop_on_none():
    """configure_logging(None) must not touch root logger level."""
    before = logging.getLogger().level
    proc_mod.configure_logging(None)
    assert logging.getLogger().level == before
