"""Scan root validation and find-symlink discovery flags."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from repo_ops_core.cli import looks_like_find_predicate, resolve_scan_roots


def test_looks_like_find_predicate() -> None:
    assert looks_like_find_predicate("-name")
    assert looks_like_find_predicate("/tmp -name .git")
    assert not looks_like_find_predicate("/usr/local/src")
    assert not looks_like_find_predicate("")


def test_resolve_scan_roots_rejects_missing(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit):
        resolve_scan_roots([str(missing)], parser)


def test_resolve_scan_roots_rejects_find_predicate(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        resolve_scan_roots(["-name", ".git"], parser)


def test_resolve_scan_roots_resolves_existing_dir(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    resolved = resolve_scan_roots([str(tmp_path)], parser)
    assert resolved == [str(tmp_path.resolve())]


# ---------------------------------------------------------------------------
# Default-roots fix: a missing DEFAULT root is skipped, not fatal (first-run
# experience on a machine without this built-in layout) — but only when the
# caller didn't name it explicitly, and only up to "all defaults missing".
# ---------------------------------------------------------------------------


def test_resolve_scan_roots_partial_defaults_returns_only_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / "exists"
    existing.mkdir()
    missing = tmp_path / "nope"
    fake_defaults = [str(existing), str(missing)]
    monkeypatch.setattr("repo_ops_core.cli.DEFAULT_ROOTS", fake_defaults)

    parser = argparse.ArgumentParser()
    resolved = resolve_scan_roots(fake_defaults, parser)

    assert resolved == [str(existing.resolve())]


def test_resolve_scan_roots_all_defaults_missing_exits_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_defaults = [str(tmp_path / "a"), str(tmp_path / "b")]
    monkeypatch.setattr("repo_ops_core.cli.DEFAULT_ROOTS", fake_defaults)

    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        resolve_scan_roots(fake_defaults, parser)

    err = capsys.readouterr().err
    assert "none of the default roots exist on this machine" in err
    assert "e.g. `.` for the current checkout" in err


def test_resolve_scan_roots_explicit_missing_path_still_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly-named missing path is a typo worth failing on, unlike a
    missing built-in default — even when some default roots do exist."""
    existing = tmp_path / "exists"
    existing.mkdir()
    monkeypatch.setattr("repo_ops_core.cli.DEFAULT_ROOTS", [str(existing)])

    parser = argparse.ArgumentParser()
    with pytest.raises(SystemExit):
        resolve_scan_roots([str(tmp_path / "typo")], parser)
