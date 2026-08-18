"""Coverage for `repo-ops inventory --status` and its git status collection.

Fixtures are real git repositories driven by real git commands: the parser
under test consumes ``git status --porcelain=v1 --branch -z`` output, and a
hand-written string fixture would only prove the parser agrees with itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repo_ops_inventory.scan import main as inventory_main
from repo_ops_status.report import summarize
from repo_ops_status.status import DETACHED, collect_status


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _clone_with_remote(tmp_path: Path, name: str, init_git_repo) -> tuple[Path, Path]:
    """Create an origin plus a working clone of it; returns ``(origin, clone)``."""
    origin = tmp_path / f"{name}.git"
    seed = tmp_path / f"{name}-seed"
    init_git_repo(seed, commit=True)
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(seed), str(origin)],
        check=True,
        capture_output=True,
    )
    clone = tmp_path / name
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        capture_output=True,
    )
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "test")
    return origin, clone


def _commit(repo: Path, filename: str, content: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", f"add {filename}")


def test_clean_clone_reports_zero_ahead_and_behind(tmp_path: Path, init_git_repo) -> None:
    _, clone = _clone_with_remote(tmp_path, "clean", init_git_repo)

    status = collect_status(str(clone), "clean")

    assert (status.ahead, status.behind, status.dirty) == (0, 0, 0)
    assert status.upstream == "origin/main" or status.upstream.startswith("origin/")
    assert status.comparable


def test_local_commits_count_as_ahead(tmp_path: Path, init_git_repo) -> None:
    _, clone = _clone_with_remote(tmp_path, "ahead", init_git_repo)
    _commit(clone, "a.txt", "a\n")
    _commit(clone, "b.txt", "b\n")

    status = collect_status(str(clone), "ahead")

    assert (status.ahead, status.behind) == (2, 0)


def test_remote_commits_count_as_behind(tmp_path: Path, init_git_repo) -> None:
    _, clone = _clone_with_remote(tmp_path, "behind", init_git_repo)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "behind.git"), str(other)],
        check=True,
        capture_output=True,
    )
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "test")
    _commit(other, "upstream.txt", "u\n")
    _git(other, "push", "-q", "origin", "HEAD")
    _git(clone, "fetch", "-q")

    status = collect_status(str(clone), "behind")

    assert (status.ahead, status.behind) == (0, 1)


def test_dirty_counts_each_entry_once_including_renames(tmp_path: Path, init_git_repo) -> None:
    """A staged rename is one dirty entry, not two, despite its second -z path."""
    _, clone = _clone_with_remote(tmp_path, "dirty", init_git_repo)
    _commit(clone, "old.txt", "content\n")
    _git(clone, "mv", "old.txt", "new.txt")
    (clone / "untracked.txt").write_text("u\n", encoding="utf-8")

    status = collect_status(str(clone), "dirty")

    assert status.dirty == 2


def test_detached_head_is_not_scored_against_origin_head(tmp_path: Path, init_git_repo) -> None:
    """A submodule parked at its pin must not read as drifted."""
    _, clone = _clone_with_remote(tmp_path, "pinned", init_git_repo)
    pinned = _git(clone, "rev-parse", "HEAD")
    _commit(clone, "later.txt", "later\n")
    _git(clone, "push", "-q", "origin", "HEAD")
    _git(clone, "checkout", "-q", pinned)

    status = collect_status(str(clone), "pinned")

    assert status.branch == DETACHED
    assert (status.ahead, status.behind) == (None, None)
    assert not status.comparable


def test_branch_without_upstream_falls_back_to_origin_head(tmp_path: Path, init_git_repo) -> None:
    _, clone = _clone_with_remote(tmp_path, "noupstream", init_git_repo)
    _git(clone, "checkout", "-q", "-b", "feature")
    _commit(clone, "f.txt", "f\n")

    status = collect_status(str(clone), "noupstream")

    assert status.branch == "feature"
    assert status.ahead == 1
    assert status.upstream is not None


def test_repo_without_remote_is_not_comparable(tmp_path: Path, init_git_repo) -> None:
    solo = tmp_path / "solo"
    init_git_repo(solo, commit=True)

    status = collect_status(str(solo), "solo")

    assert (status.ahead, status.behind) == (None, None)
    assert status.dirty == 0


def test_empty_repo_reports_branch_without_counts(tmp_path: Path, init_git_repo) -> None:
    empty = tmp_path / "empty"
    init_git_repo(empty)

    status = collect_status(str(empty), "empty")

    assert status.empty
    assert (status.ahead, status.behind) == (None, None)


def test_summarize_separates_unmeasurable_from_zero(tmp_path: Path, init_git_repo) -> None:
    _, clone = _clone_with_remote(tmp_path, "measured", init_git_repo)
    _commit(clone, "a.txt", "a\n")
    solo = tmp_path / "solo"
    init_git_repo(solo, commit=True)

    rows = [collect_status(str(clone), "measured"), collect_status(str(solo), "solo")]
    summary = summarize(rows)

    assert summary["repos"] == 2
    assert summary["repos_ahead"] == 1
    assert summary["ahead_commits"] == 1
    assert summary["repos_no_upstream"] == 1


# ---------- CLI wiring ----------


def test_status_mode_emits_one_row_per_repo(tmp_path: Path, capsys, init_git_repo) -> None:
    org = tmp_path / "org"
    init_git_repo(org / "alpha", commit=True)
    init_git_repo(org / "zeta", commit=True)

    rc = inventory_main([str(tmp_path), "--status", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "status"
    assert payload["fetched"] is False
    assert [r["repo"] for r in payload["rows"]] == ["org/alpha", "org/zeta"]


def test_status_mode_human_table_has_the_five_columns(
    tmp_path: Path, capsys, init_git_repo
) -> None:
    init_git_repo(tmp_path / "org" / "alpha", commit=True)

    rc = inventory_main([str(tmp_path), "--status"])

    assert rc == 0
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if line.startswith("repo"))
    assert header.split() == ["repo", "branch", "dirty", "ahead", "behind"]
    assert "org/alpha" in out


def test_fetch_without_status_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        inventory_main([str(tmp_path), "--fetch"])

    assert excinfo.value.code == 2


def test_colliding_labels_across_roots_keep_both_repos(tmp_path: Path, init_git_repo) -> None:
    """Same root-relative path under two roots must not collapse to one row."""
    from repo_ops_status.status import repo_paths_to_labels

    left = tmp_path / "left"
    right = tmp_path / "right"
    init_git_repo(left / "org" / "same", commit=True)
    init_git_repo(right / "org" / "same", commit=True)

    discovered = {
        str(left / "org" / "same" / ".git"): str(left / "org" / "same"),
        str(right / "org" / "same" / ".git"): str(right / "org" / "same"),
    }
    labelled = repo_paths_to_labels(discovered, [str(left), str(right)])

    assert len(labelled) == 2
    assert len({label for label, _ in labelled}) == 2
