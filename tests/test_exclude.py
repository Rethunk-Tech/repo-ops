from pathlib import Path

from repo_ops_core.exclude import (
    filter_repo_paths,
    merge_exclude_patterns,
    repo_matches_exclude,
)


def test_repo_matches_exclude_supports_substrings_and_globs() -> None:
    assert repo_matches_exclude("org/vendor-repo", "/src/vendor-repo", ["vendor"])
    assert repo_matches_exclude("org/vendor-repo", "/src/vendor-repo", ["org/*"])
    assert not repo_matches_exclude("org/keep-repo", "/src/keep-repo", ["vendor"])


def test_merge_exclude_patterns_prefers_non_empty_cli() -> None:
    assert merge_exclude_patterns(["cli"], ["config"]) == ["cli"]
    assert merge_exclude_patterns([], ["config"]) == ["config"]
    assert merge_exclude_patterns(None, "config") == ["config"]


def test_filter_repo_paths_returns_kept_paths_and_count(tmp_path: Path) -> None:
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    paths = [keep, drop]

    kept, excluded = filter_repo_paths(paths, ["drop"])

    assert kept == [keep]
    assert excluded == 1
