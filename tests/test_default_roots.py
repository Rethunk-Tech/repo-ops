"""default_roots() follows `bosun roots` when bosun is installed."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from repo_ops_core.constants import _FALLBACK_ROOTS, default_roots


def _bosun(tmp_path: Path, body: str) -> None:
    exe = tmp_path / "bosun"
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)


def test_uses_bosun_roots_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bosun(tmp_path, '[ "$1" = roots ] && printf "/a\\n/b\\n"')
    monkeypatch.setenv("PATH", str(tmp_path))
    assert default_roots() == ["/a", "/b"]


def test_falls_back_without_bosun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert default_roots() == _FALLBACK_ROOTS


def test_falls_back_when_bosun_fails_or_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    _bosun(tmp_path, "exit 1")
    assert default_roots() == _FALLBACK_ROOTS
    _bosun(tmp_path, "true")
    assert default_roots() == _FALLBACK_ROOTS
