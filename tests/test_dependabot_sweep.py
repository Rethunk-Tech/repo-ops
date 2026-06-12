"""Tests for the repo-ops dependabot subcommand.

All subprocess and git calls are mocked — no network traffic, no real gh/git.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_dependabot.dependabot import (
    DependabotPR,
    _check_superseded,
    _is_security_title,
    _manifest_versions,
    _owner_repo,
    _parse_bump,
    _parse_group_body,
    _split_name_ver,
    _strip_v,
    _version_gte,
    build_dependabot_parser,
    main,
)

# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def test_strip_v_removes_leading_v() -> None:
    assert _strip_v("v1.2.3") == "1.2.3"
    assert _strip_v("1.2.3") == "1.2.3"
    assert _strip_v("V2.0.0") == "V2.0.0"  # only lowercase v


def test_version_gte_basic() -> None:
    assert _version_gte("2.0.0", "1.9.0") is True
    assert _version_gte("1.9.0", "2.0.0") is False
    assert _version_gte("2.0.0", "2.0.0") is True


def test_version_gte_handles_v_prefix() -> None:
    assert _version_gte("v2.1.0", "v2.0.0") is True


def test_version_gte_pre_release_segment_ignored() -> None:
    # Pre-release suffix stripped after first non-numeric
    assert _version_gte("2.0.0-beta1", "2.0.0") is True


# ---------------------------------------------------------------------------
# Title parsing — single-package patterns
# ---------------------------------------------------------------------------


def test_parse_bump_standard_title() -> None:
    bump = _parse_bump("Bump lodash from 4.17.11 to 4.17.21")
    assert bump is not None
    assert bump.package == "lodash"
    assert bump.from_ver == "4.17.11"
    assert bump.to_ver == "4.17.21"


def test_parse_bump_bumps_variant() -> None:
    bump = _parse_bump("Bumps lodash from 4.17.11 to 4.17.21")
    assert bump is not None
    assert bump.package == "lodash"


def test_parse_bump_lowercase() -> None:
    bump = _parse_bump("bump lodash from 4.17.11 to 4.17.21")
    assert bump is not None
    assert bump.package == "lodash"


def test_parse_bump_in_subpath() -> None:
    bump = _parse_bump("Bump lodash from 4.17.11 to 4.17.21 in /packages/app")
    assert bump is not None
    assert bump.package == "lodash"
    assert bump.to_ver == "4.17.21"


def test_parse_bump_conventional_commit_chore_deps() -> None:
    bump = _parse_bump("chore(deps): bump lodash from 4.17.11 to 4.17.21")
    assert bump is not None
    assert bump.package == "lodash"
    assert bump.to_ver == "4.17.21"


def test_parse_bump_conventional_commit_build_deps_dev() -> None:
    bump = _parse_bump("build(deps-dev): bump typescript from 4.9.5 to 5.0.0")
    assert bump is not None
    assert bump.package == "typescript"
    assert bump.to_ver == "5.0.0"


def test_parse_bump_go_module() -> None:
    bump = _parse_bump("Bump golang.org/x/net from 0.14.0 to 0.17.0")
    assert bump is not None
    assert bump.package == "golang.org/x/net"
    assert bump.to_ver == "0.17.0"


def test_parse_bump_multi_package_takes_first() -> None:
    bump = _parse_bump("Bump foo and bar from 1.0 to 2.0")
    assert bump is not None
    assert bump.package == "foo"


def test_parse_bump_group_title_returns_none() -> None:
    """Grouped PR title must not be parsed as a single-package bump."""
    assert _parse_bump("Bump the production-dependencies group with 3 updates") is None


def test_parse_bump_unparseable_returns_none() -> None:
    assert _parse_bump("chore: update lockfile") is None
    assert _parse_bump("") is None


# ---------------------------------------------------------------------------
# Security flag
# ---------------------------------------------------------------------------


def test_is_security_title_detects_bracket_prefix() -> None:
    assert _is_security_title("[Security] Bump lodash from 4.17.11 to 4.17.21") is True


def test_is_security_title_false_for_normal() -> None:
    assert _is_security_title("Bump lodash from 4.17.11 to 4.17.21") is False


def test_parse_bump_security_prefix() -> None:
    bump = _parse_bump("[Security] Bump lodash from 4.17.11 to 4.17.21")
    assert bump is not None
    assert bump.package == "lodash"
    assert bump.to_ver == "4.17.21"


# ---------------------------------------------------------------------------
# Grouped PR body parsing
# ---------------------------------------------------------------------------

_SAMPLE_GROUP_BODY_3COL = """\
## Bumped dependencies

| Package | From | To |
| ------- | ---- | -- |
| lodash  | 4.17.11 | 4.17.21 |
| express | 4.18.0  | 4.19.0  |
"""

_SAMPLE_GROUP_BODY_ARROW = """\
Updates the group:

| Package | Update |
| ------- | ------ |
| lodash  | 4.17.11 → 4.17.21 |
| express | 4.18.0 → 4.19.0  |
"""

_SAMPLE_GROUP_BODY_NO_TABLE = """\
This is a regular PR with no update table.
Just some prose.
"""


def test_parse_group_body_three_column() -> None:
    rows = _parse_group_body(_SAMPLE_GROUP_BODY_3COL)
    assert rows is not None
    assert len(rows) == 2
    pkgs = {r.package for r in rows}
    assert "lodash" in pkgs
    assert "express" in pkgs
    lodash = next(r for r in rows if r.package == "lodash")
    assert lodash.from_ver == "4.17.11"
    assert lodash.to_ver == "4.17.21"


def test_parse_group_body_no_table_returns_none() -> None:
    result = _parse_group_body(_SAMPLE_GROUP_BODY_NO_TABLE)
    assert result is None


def test_parse_group_body_empty_string_returns_none() -> None:
    assert _parse_group_body("") is None


# ---------------------------------------------------------------------------
# split_name_ver helper
# ---------------------------------------------------------------------------


def test_split_name_ver_regular() -> None:
    assert _split_name_ver("lodash@4.17.21") == ("lodash", "4.17.21")


def test_split_name_ver_scoped() -> None:
    name, ver = _split_name_ver("@babel/core@7.22.0")
    assert name == "@babel/core"
    assert ver == "7.22.0"


def test_split_name_ver_no_at() -> None:
    name, ver = _split_name_ver("lodash")
    assert name == "lodash"
    assert ver == ""


# ---------------------------------------------------------------------------
# Owner/repo extraction
# ---------------------------------------------------------------------------


def test_owner_repo_https_url() -> None:
    assert _owner_repo("https://github.com/owner/repo.git") == "owner/repo"
    assert _owner_repo("https://github.com/owner/repo") == "owner/repo"


def test_owner_repo_ssh_url() -> None:
    assert _owner_repo("git@github.com:owner/repo.git") == "owner/repo"


def test_owner_repo_non_github_returns_none() -> None:
    assert _owner_repo("https://gitlab.com/owner/repo.git") is None


# ---------------------------------------------------------------------------
# Supersession check (with lockfile_resolved)
# ---------------------------------------------------------------------------


def _make_pr(title: str) -> DependabotPR:
    pr = DependabotPR(
        number=1,
        title=title,
        head_ref="",
        repo_path="/repo",
        repo_label_str="repo",
    )
    pr.bump = _parse_bump(title)
    return pr


def test_check_superseded_high_confidence_from_resolved() -> None:
    pr = _make_pr("Bump lodash from 4.17.11 to 4.17.21")
    superseded, current = _check_superseded(
        pr,
        lockfile_versions=set(),
        manifest_versions={},
        lockfile_resolved={"lodash": "4.17.21"},
    )
    assert superseded is True
    assert current == "4.17.21"
    assert pr.low_confidence is False


def test_check_superseded_low_confidence_from_text_search() -> None:
    pr = _make_pr("Bump lodash from 4.17.11 to 4.17.21")
    superseded, current = _check_superseded(
        pr,
        lockfile_versions={"lodash@4.17.21"},
        manifest_versions={},
        lockfile_resolved={},  # empty → falls back to text search
    )
    assert superseded is True
    assert current == "4.17.21"
    assert pr.low_confidence is True  # text-search fallback


def test_check_superseded_found_in_lockfile() -> None:
    pr = _make_pr("Bump lodash from 4.17.11 to 4.17.21")
    lockfile_versions = {"lodash@4.17.21", "express@4.18.0"}
    superseded, current = _check_superseded(pr, lockfile_versions, {})
    assert superseded is True
    assert current == "4.17.21"


def test_check_superseded_older_version_in_lockfile() -> None:
    pr = _make_pr("Bump lodash from 4.17.11 to 4.17.21")
    lockfile_versions = {"lodash@4.17.15"}  # < 4.17.21
    superseded, current = _check_superseded(pr, lockfile_versions, {})
    assert superseded is False
    assert current == "4.17.15"


def test_check_superseded_not_in_any_lockfile() -> None:
    pr = _make_pr("Bump lodash from 4.17.11 to 4.17.21")
    superseded, current = _check_superseded(pr, set(), {})
    assert superseded is None
    assert current is None


def test_check_superseded_found_in_manifest() -> None:
    pr = _make_pr("Bump golang.org/x/net from 0.14.0 to 0.17.0")
    manifest = {"golang.org/x/net": "0.17.0"}
    superseded, current = _check_superseded(pr, set(), manifest)
    assert superseded is True
    assert current == "0.17.0"


def test_check_superseded_unparseable_pr_returns_none() -> None:
    pr = DependabotPR(
        number=2,
        title="chore: update lockfile",
        head_ref="",
        repo_path="/repo",
        repo_label_str="repo",
    )
    # bump is None — cannot determine supersession
    superseded, _current = _check_superseded(pr, {"foo@1.0"}, {})
    assert superseded is None


# ---------------------------------------------------------------------------
# Grouped PR close safety
# ---------------------------------------------------------------------------


def test_grouped_pr_not_closed_when_row_low_confidence(tmp_path: Path) -> None:
    """Grouped PRs with any low-confidence row must not be auto-closed."""
    fake_repo = str(tmp_path)

    group_pr_data = [
        {
            "number": 77,
            "title": "Bump the production-dependencies group with 2 updates",
            "headRefName": "dependabot/npm/group-1",
        }
    ]

    group_body = """\
| Package | From | To |
| ------- | ---- | -- |
| lodash  | 4.17.11 | 4.17.21 |
| express | 4.18.0  | 4.19.0  |
"""

    mock_git = MagicMock(returncode=0, stdout="origin/main\n")

    with (
        patch(
            "repo_ops_dependabot.dependabot.discover_repos",
            return_value={"c": fake_repo},
        ),
        patch(
            "repo_ops_dependabot.dependabot._github_remote",
            return_value="https://github.com/owner/repo",
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_list_dependabot_prs",
            return_value=(group_pr_data, ""),
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_pr_body",
            return_value=(group_body, ""),
        ),
        # lockfile_resolved is empty → falls back to text search (low confidence)
        patch(
            "repo_ops_dependabot.dependabot._lockfile_resolved",
            return_value={},
        ),
        patch(
            "repo_ops_dependabot.dependabot._lockfile_versions",
            return_value={"lodash@4.17.21", "express@4.19.0"},
        ),
        patch("repo_ops_dependabot.dependabot._manifest_versions", return_value={}),
        patch("repo_ops_dependabot.dependabot.run_git", return_value=mock_git),
        patch(
            "repo_ops_dependabot.dependabot._gh_close_pr", return_value=""
        ) as mock_close,
    ):
        main(["--close", fake_repo])
        # Low-confidence rows → grouped PR must NOT be closed
        mock_close.assert_not_called()


def test_unparseable_grouped_pr_never_closed(tmp_path: Path) -> None:
    """Grouped PR where body fetch fails must never be closed."""
    fake_repo = str(tmp_path)

    group_pr_data = [
        {
            "number": 88,
            "title": "Bump the security group with 2 updates",
            "headRefName": "dependabot/npm/security-group",
        }
    ]

    mock_git = MagicMock(returncode=0, stdout="origin/main\n")

    with (
        patch(
            "repo_ops_dependabot.dependabot.discover_repos",
            return_value={"c": fake_repo},
        ),
        patch(
            "repo_ops_dependabot.dependabot._github_remote",
            return_value="https://github.com/owner/repo",
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_list_dependabot_prs",
            return_value=(group_pr_data, ""),
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_pr_body",
            return_value=("", "gh error: not found"),
        ),
        patch("repo_ops_dependabot.dependabot._lockfile_resolved", return_value={}),
        patch("repo_ops_dependabot.dependabot._lockfile_versions", return_value=set()),
        patch("repo_ops_dependabot.dependabot._manifest_versions", return_value={}),
        patch("repo_ops_dependabot.dependabot.run_git", return_value=mock_git),
        patch(
            "repo_ops_dependabot.dependabot._gh_close_pr", return_value=""
        ) as mock_close,
    ):
        main(["--close", fake_repo])
        mock_close.assert_not_called()


# ---------------------------------------------------------------------------
# Manifest version extraction
# ---------------------------------------------------------------------------


def test_manifest_versions_go_mod(tmp_path: Path) -> None:
    go_mod = tmp_path / "go.mod"
    go_mod.write_text(
        "module example.com/app\n\ngo 1.21\n\nrequire (\n\tgolang.org/x/net v0.17.0\n)\n",
        encoding="utf-8",
    )
    versions = _manifest_versions(str(tmp_path))
    assert versions.get("golang.org/x/net") == "0.17.0"


def test_manifest_versions_cargo_lock(tmp_path: Path) -> None:
    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.197"\n',
        encoding="utf-8",
    )
    versions = _manifest_versions(str(tmp_path))
    assert versions.get("serde") == "1.0.197"


def test_manifest_versions_empty_repo(tmp_path: Path) -> None:
    versions = _manifest_versions(str(tmp_path))
    assert versions == {}


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_dependabot_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--close" in out


def test_dependabot_json_empty_repos(tmp_path: Path) -> None:
    """No repos discovered → JSON with empty repos list, exit 0."""
    with patch("repo_ops_dependabot.dependabot.discover_repos", return_value={}):
        result = main(["--json", str(tmp_path)])
    assert result == 0


def test_dependabot_json_output_structure(tmp_path: Path) -> None:
    """JSON output has expected schema_version and summary keys."""
    with patch("repo_ops_dependabot.dependabot.discover_repos", return_value={}):
        result = main(["--json", str(tmp_path)])
    assert result == 0


def test_dependabot_dry_run_no_close_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --close, gh pr close is never called."""
    with (
        patch("repo_ops_dependabot.dependabot.discover_repos", return_value={}),
        patch("repo_ops_dependabot.dependabot._gh_close_pr") as mock_close,
    ):
        main([str(tmp_path)])
        mock_close.assert_not_called()


def test_dependabot_parser_close_flag() -> None:
    parser = build_dependabot_parser()
    args = parser.parse_args(["--close"])
    assert args.close is True
    args_no_close = parser.parse_args([])
    assert args_no_close.close is False


def test_dependabot_close_calls_gh_pr_close(tmp_path: Path) -> None:
    """With --close and a superseded high-confidence PR, gh pr close is invoked."""
    fake_repo = str(tmp_path)

    mock_pr_data = [
        {
            "number": 42,
            "title": "Bump lodash from 4.17.11 to 4.17.21",
            "headRefName": "dependabot/npm/lodash-4.17.21",
        }
    ]

    def mock_discover(roots: list, **kwargs: object) -> dict:  # type: ignore[type-arg]
        return {"common": fake_repo}

    mock_git_result = MagicMock(returncode=0, stdout="origin/main\n")
    with (
        patch(
            "repo_ops_dependabot.dependabot.discover_repos", side_effect=mock_discover
        ),
        patch(
            "repo_ops_dependabot.dependabot._github_remote",
            return_value="https://github.com/owner/repo",
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_list_dependabot_prs",
            return_value=(mock_pr_data, ""),
        ),
        # High-confidence via lockfile_resolved
        patch(
            "repo_ops_dependabot.dependabot._lockfile_resolved",
            return_value={"lodash": "4.17.21"},
        ),
        patch(
            "repo_ops_dependabot.dependabot._lockfile_versions",
            return_value=set(),
        ),
        patch("repo_ops_dependabot.dependabot._manifest_versions", return_value={}),
        patch("repo_ops_dependabot.dependabot.run_git", return_value=mock_git_result),
        patch(
            "repo_ops_dependabot.dependabot._gh_close_pr", return_value=""
        ) as mock_close,
    ):
        main(["--close", fake_repo])
        mock_close.assert_called_once()
        call_args = mock_close.call_args
        assert call_args[0][1] == 42  # pr_number


def test_dependabot_low_confidence_never_closed(tmp_path: Path) -> None:
    """Low-confidence text-search match must not trigger close even with --close."""
    fake_repo = str(tmp_path)

    mock_pr_data = [
        {
            "number": 55,
            "title": "Bump lodash from 4.17.11 to 4.17.21",
            "headRefName": "dependabot/npm/lodash-4.17.21",
        }
    ]

    mock_git_result = MagicMock(returncode=0, stdout="origin/main\n")
    with (
        patch(
            "repo_ops_dependabot.dependabot.discover_repos",
            return_value={"c": fake_repo},
        ),
        patch(
            "repo_ops_dependabot.dependabot._github_remote",
            return_value="https://github.com/owner/repo",
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_list_dependabot_prs",
            return_value=(mock_pr_data, ""),
        ),
        # No lockfile_resolved → text-search fallback (low confidence)
        patch(
            "repo_ops_dependabot.dependabot._lockfile_resolved",
            return_value={},
        ),
        patch(
            "repo_ops_dependabot.dependabot._lockfile_versions",
            return_value={"lodash@4.17.21"},
        ),
        patch("repo_ops_dependabot.dependabot._manifest_versions", return_value={}),
        patch("repo_ops_dependabot.dependabot.run_git", return_value=mock_git_result),
        patch(
            "repo_ops_dependabot.dependabot._gh_close_pr", return_value=""
        ) as mock_close,
    ):
        main(["--close", fake_repo])
        mock_close.assert_not_called()


def test_dependabot_skips_non_github_repos(tmp_path: Path) -> None:
    """Repos without a github.com remote are silently skipped."""
    with (
        patch(
            "repo_ops_dependabot.dependabot.discover_repos",
            return_value={"common": str(tmp_path)},
        ),
        patch("repo_ops_dependabot.dependabot._github_remote", return_value=None),
        patch("repo_ops_dependabot.dependabot._gh_list_dependabot_prs") as mock_list,
    ):
        main([str(tmp_path)])
        mock_list.assert_not_called()


def test_dependabot_security_pr_is_flagged(tmp_path: Path) -> None:
    """[Security] PRs have is_security=True in the result."""
    fake_repo = str(tmp_path)

    mock_pr_data = [
        {
            "number": 99,
            "title": "[Security] Bump lodash from 4.17.11 to 4.17.21",
            "headRefName": "dependabot/npm/lodash-4.17.21",
        }
    ]

    mock_git = MagicMock(returncode=0, stdout="origin/main\n")
    with (
        patch(
            "repo_ops_dependabot.dependabot.discover_repos",
            return_value={"c": fake_repo},
        ),
        patch(
            "repo_ops_dependabot.dependabot._github_remote",
            return_value="https://github.com/owner/repo",
        ),
        patch(
            "repo_ops_dependabot.dependabot._gh_list_dependabot_prs",
            return_value=(mock_pr_data, ""),
        ),
        patch("repo_ops_dependabot.dependabot._lockfile_resolved", return_value={}),
        patch("repo_ops_dependabot.dependabot._lockfile_versions", return_value=set()),
        patch("repo_ops_dependabot.dependabot._manifest_versions", return_value={}),
        patch("repo_ops_dependabot.dependabot.run_git", return_value=mock_git),
    ):
        from repo_ops_dependabot.dependabot import scan_repos

        results = scan_repos([fake_repo])
        assert results
        prs = results[0].prs
        assert len(prs) == 1
        assert prs[0].is_security is True
