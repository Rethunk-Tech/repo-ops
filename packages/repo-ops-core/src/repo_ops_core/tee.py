"""Duplicate stdout/stderr to a log file (pipefail-safe automation)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class _TeeIO:
    """Write-through wrapper that mirrors bytes to a log file."""

    def __init__(self, stream, log_file) -> None:
        self._stream = stream
        self._log = log_file

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._stream.write(data)
        self._log.write(data)
        return len(data)

    def flush(self) -> None:
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


@contextmanager
def tee_stdout_stderr(log_path: Path) -> Iterator[None]:
    """Mirror sys.stdout and sys.stderr to log_path for the context duration."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8", newline="") as log_file:
        out_tee = _TeeIO(sys.stdout, log_file)
        err_tee = _TeeIO(sys.stderr, log_file)
        prev_out, prev_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_tee, err_tee
        try:
            yield
        finally:
            out_tee.flush()
            err_tee.flush()
            sys.stdout, sys.stderr = prev_out, prev_err
