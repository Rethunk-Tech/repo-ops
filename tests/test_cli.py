"""Tests for CLI helpers in repo_ops.cli."""

import argparse

import pytest

from repo_ops.core.cli import parse_iso8601


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
