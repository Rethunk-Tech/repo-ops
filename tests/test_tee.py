"""Tests for tee_stdout_stderr."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from unittest.mock import patch

from repo_ops_core.tee import _TeeIO, redact_secrets, tee_stdout_stderr


def test_tee_restores_streams_when_flush_raises(tmp_path: Path) -> None:
    """If flush raises, sys.stdout/sys.stderr must still be restored."""
    log = tmp_path / "out.log"
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr

    def raising_flush(self) -> None:
        raise OSError("disk full")

    with (
        patch.object(_TeeIO, "flush", raising_flush),
        contextlib.suppress(Exception),
        tee_stdout_stderr(log),
    ):
        pass

    assert sys.stdout is orig_stdout
    assert sys.stderr is orig_stderr


def test_tee_redacts_secrets_in_log(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    with tee_stdout_stderr(log):
        print("Authorization: Bearer supersecret")
        print("token=abc123", file=sys.stderr)
    text = log.read_text(encoding="utf-8")
    assert "supersecret" not in text
    assert "abc123" not in text
    assert "***" in text
    assert redact_secrets("npm_abcdefghijklmnopqrstuvwxyz") == "***"
