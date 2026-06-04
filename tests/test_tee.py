"""Tests for tee_stdout_stderr (#55 — flush errors must not break stream restore)."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from unittest.mock import patch

from repo_ops_core.tee import _TeeIO, tee_stdout_stderr


def test_tee_restores_streams_when_flush_raises(tmp_path: Path) -> None:
    """#55 — if flush raises, sys.stdout/sys.stderr must still be restored."""
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
