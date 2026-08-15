"""Tests for the parallel git-common-dir resolution path in discover_repos."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.discovery import (
    _resolve_common_dirs,
    _resolve_one,
    discover_repos,
)

# ---------------------------------------------------------------------------
# _resolve_one
# ---------------------------------------------------------------------------


def test_resolve_one_returns_common_on_success(tmp_path: Path) -> None:
    """_resolve_one passes the result of git_common_dir through cleanly."""
    expected = "/some/common/.git"
    with patch("repo_ops_core.discovery.git_common_dir", return_value=expected):
        repo, common = _resolve_one(str(tmp_path))
    assert repo == str(tmp_path)
    assert common == expected


def test_resolve_one_timeout_returns_none_and_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(_: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)

    with patch("repo_ops_core.discovery.git_common_dir", _raise):
        repo, common = _resolve_one(str(tmp_path))

    assert repo == str(tmp_path)
    assert common is None
    assert "WARN" in capsys.readouterr().err


def test_resolve_one_fnf_returns_none_and_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(_: object) -> None:
        raise FileNotFoundError("gone")

    with patch("repo_ops_core.discovery.git_common_dir", _raise):
        repo, common = _resolve_one(str(tmp_path))

    assert repo == str(tmp_path)
    assert common is None
    assert "WARN" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _resolve_common_dirs — ordering determinism
# ---------------------------------------------------------------------------


def test_resolve_common_dirs_preserves_find_order_despite_shuffled_completion(
    tmp_path: Path,
) -> None:
    """Dedup must honour the original find order (first-seen wins), even when
    threads complete in a different sequence.

    Two repos share the same git-common-dir and have equal-length paths so
    the path-length tiebreaker cannot mask an ordering bug.  repo_a comes
    first in the input list and must win the dedup slot.

    We guarantee repo_b's thread returns *before* repo_a's by blocking
    repo_a until repo_b has signalled completion, so as_completed yields
    repo_b first — but the post-sort must still place repo_a ahead.
    """
    # Equal-length names so the len(repo) tiebreaker cannot affect the result.
    repo_a = str(tmp_path / "rrr")
    repo_b = str(tmp_path / "sss")
    shared_common = str(tmp_path / "shared.git")

    # b_done is set by repo_b's thread; repo_a waits on it so b completes first.
    b_done = threading.Event()

    def slow_git_common_dir(p: object) -> str:
        if str(p) == repo_a:
            b_done.wait(timeout=5)  # block until repo_b has already resolved
        resolved = shared_common
        if str(p) == repo_b:
            b_done.set()
        return resolved

    with patch("repo_ops_core.discovery.git_common_dir", slow_git_common_dir):
        result = _resolve_common_dirs([repo_a, repo_b])

    # repo_a is index 0 → first-seen → must win even though it resolved later.
    assert result == {shared_common: repo_a}, (
        f"expected first-seen (repo_a) to win dedup; got {result}"
    )


def test_resolve_common_dirs_dedup_shortest_path_wins_same_common(
    tmp_path: Path,
) -> None:
    """When two repos share a common-dir AND one path is shorter, the shorter wins."""
    repo_long = str(tmp_path / "org" / "longer-path" / "repo")
    repo_short = str(tmp_path / "org" / "repo")
    shared_common = str(tmp_path / "shared.git")

    with patch("repo_ops_core.discovery.git_common_dir", return_value=shared_common):
        result = _resolve_common_dirs([repo_long, repo_short])

    assert result == {shared_common: repo_short}, (
        f"shorter path must win dedup; got {result}"
    )


def test_resolve_common_dirs_empty_input_returns_empty() -> None:
    result = _resolve_common_dirs([])
    assert result == {}


def test_resolve_common_dirs_none_common_excluded(tmp_path: Path) -> None:
    """Repos where git_common_dir returns None are silently dropped."""
    repo = str(tmp_path / "repo")

    with patch("repo_ops_core.discovery.git_common_dir", return_value=None):
        result = _resolve_common_dirs([repo])

    assert result == {}


# ---------------------------------------------------------------------------
# discover_repos — parallel path via integration with tmp_path layout
# ---------------------------------------------------------------------------


def test_discover_repos_parallel_resolves_multiple_repos(tmp_path: Path) -> None:
    """discover_repos must resolve all repos when multiple .git dirs exist."""
    repos = []
    commons = []
    for name in ("alpha", "beta", "gamma"):
        r = tmp_path / "org" / name
        (r / ".git").mkdir(parents=True)
        repos.append(str(r.resolve()))
        commons.append(str((r / ".git").resolve()))

    def fake_git_common_dir(p: object) -> str:
        idx = repos.index(str(p))
        return commons[idx]

    with patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir):
        result = discover_repos([str(tmp_path)])

    assert set(result.keys()) == set(commons), (
        f"all repos must be discovered; got {result}"
    )


def test_discover_repos_parallel_ordering_is_deterministic(tmp_path: Path) -> None:
    """Running discover_repos twice on the same tree must return identical results."""
    for name in ("r1", "r2", "r3"):
        (tmp_path / name / ".git").mkdir(parents=True)
    common_base = str((tmp_path / "shared.git").resolve())

    call_count = 0
    lock = threading.Lock()

    def fake_git_common_dir(_: object) -> str:
        nonlocal call_count
        with lock:
            call_count += 1
        return common_base

    with patch("repo_ops_core.discovery.git_common_dir", fake_git_common_dir):
        result1 = discover_repos([str(tmp_path)])
        result2 = discover_repos([str(tmp_path)])

    assert result1 == result2, (
        "discover_repos must be deterministic across calls; "
        f"first={result1} second={result2}"
    )
