"""Tests for submodule tag-advance detection and application.

Covers:
- _is_semver_tag / _resolve_latest_submodule_tag (pure logic)
- _fetch_remote_tags (mocked subprocess)
- check_submodule_tag_advances (mocked tag fetch + describe)
- apply_submodule_tag_advances (mocked run_git, git ops verified)
- SubmoduleLink.url populated from discover_submodules_under_roots
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_upgrade.submodules import (
    _REPORT_ONLY_PREFIX,
    SubmoduleLink,
    SubmoduleTagBump,
    _clear_submodule_tag_cache,
    _fetch_remote_tags,
    _is_semver_tag,
    _resolve_latest_submodule_tag,
    apply_submodule_tag_advances,
    check_submodule_tag_advances,
    discover_submodules_under_roots,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the in-process tag cache before (and after) each test."""
    _clear_submodule_tag_cache()
    yield
    _clear_submodule_tag_cache()


def _make_link(
    tmp_path: Path,
    *,
    path: str = "sub",
    url: str = "https://example.com/sub.git",
) -> SubmoduleLink:
    git_repo = (tmp_path / path).resolve()
    git_repo.mkdir(parents=True, exist_ok=True)
    return SubmoduleLink(
        path=path, git_repo=git_repo, parent=tmp_path.resolve(), url=url
    )


def _ok_proc(stdout: str = "") -> MagicMock:
    p = MagicMock()
    p.returncode = 0
    p.stdout = stdout
    return p


def _fail_proc() -> MagicMock:
    p = MagicMock()
    p.returncode = 1
    p.stdout = ""
    return p


# ---------------------------------------------------------------------------
# _is_semver_tag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.2.3", True),
        ("1.2.3", True),
        ("v10.0.0", True),
        ("v1.0.0-rc1", True),
        ("1.0.0-alpha.1", True),
        ("main", False),
        ("master", False),
        ("", False),
        # Bare hex strings that look like SHAs must not be classified as semver
        ("abc1234", False),
    ],
)
def test_is_semver_tag(tag: str, expected: bool) -> None:
    assert _is_semver_tag(tag) is expected


# ---------------------------------------------------------------------------
# _resolve_latest_submodule_tag
# ---------------------------------------------------------------------------


def test_resolve_latest_picks_newest_in_same_major() -> None:
    tags = ["v1.0.0", "v1.2.3", "v1.3.0", "v2.0.0"]
    assert _resolve_latest_submodule_tag("v1.2.3", tags) == "v1.3.0"


def test_resolve_latest_no_cross_major_by_default() -> None:
    # v2.0.0 exists but cross-major is off; no candidate within v1.x > v1.2.3.
    tags = ["v1.0.0", "v1.2.3", "v2.0.0"]
    assert _resolve_latest_submodule_tag("v1.2.3", tags) is None


def test_resolve_latest_cross_major_opt_in() -> None:
    tags = ["v1.2.3", "v2.0.0", "v3.1.0"]
    result = _resolve_latest_submodule_tag("v1.2.3", tags, allow_cross_major=True)
    assert result == "v3.1.0"


def test_resolve_latest_already_at_latest() -> None:
    tags = ["v1.2.2", "v1.2.3"]
    assert _resolve_latest_submodule_tag("v1.2.3", tags) is None


def test_resolve_latest_non_semver_current_returns_none() -> None:
    tags = ["v1.2.3", "v1.2.4"]
    assert _resolve_latest_submodule_tag("main", tags) is None


def test_resolve_latest_bare_numeric_prefix_style_respected() -> None:
    # v-prefixed tags should not be selected when the current tag is bare.
    tags = ["1.0.0", "1.1.0", "v1.2.0"]
    result = _resolve_latest_submodule_tag("1.0.0", tags)
    assert result == "1.1.0"


def test_resolve_latest_empty_tags_returns_none() -> None:
    assert _resolve_latest_submodule_tag("v1.0.0", []) is None


# ---------------------------------------------------------------------------
# _fetch_remote_tags
# ---------------------------------------------------------------------------


def test_fetch_remote_tags_parses_ls_remote_output() -> None:
    stdout = (
        "abc123\trefs/tags/v1.0.0\ndef456\trefs/tags/v1.1.0\nghi789\trefs/tags/v2.0.0\n"
    )
    with patch("subprocess.run", return_value=_ok_proc(stdout)):
        tags = _fetch_remote_tags("https://example.com/repo.git")
    assert tags == ["v1.0.0", "v1.1.0", "v2.0.0"]


def test_fetch_remote_tags_nonzero_exit_returns_none() -> None:
    with patch("subprocess.run", return_value=_fail_proc()):
        tags = _fetch_remote_tags("https://example.com/repo.git")
    assert tags is None


def test_fetch_remote_tags_timeout_returns_none() -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 30)):
        tags = _fetch_remote_tags("https://example.com/repo.git")
    assert tags is None


def test_fetch_remote_tags_file_not_found_returns_none() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        tags = _fetch_remote_tags("https://example.com/repo.git")
    assert tags is None


def test_fetch_remote_tags_cached(tmp_path: Path) -> None:
    """Second call with the same URL must not re-invoke subprocess."""
    stdout = "abc\trefs/tags/v1.0.0\n"
    with patch("subprocess.run", return_value=_ok_proc(stdout)) as mock_run:
        _fetch_remote_tags("https://example.com/repo.git")
        _fetch_remote_tags("https://example.com/repo.git")
    assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# check_submodule_tag_advances
# ---------------------------------------------------------------------------


def test_check_no_url_silently_skipped(tmp_path: Path) -> None:
    link = SubmoduleLink(
        path="sub",
        git_repo=(tmp_path / "sub").resolve(),
        parent=tmp_path.resolve(),
        url="",
    )
    (tmp_path / "sub").mkdir()
    bumps = check_submodule_tag_advances([link])
    assert bumps == []


def test_check_sha_only_pin_is_report_only(tmp_path: Path) -> None:
    link = _make_link(tmp_path)
    with patch("repo_ops_upgrade.submodules._current_submodule_tag", return_value=None):
        bumps = check_submodule_tag_advances([link])
    assert len(bumps) == 1
    assert not bumps[0].is_actionable
    assert _REPORT_ONLY_PREFIX in bumps[0].latest_tag
    assert "no exact tag" in bumps[0].latest_tag
    assert bumps[0].current_tag == ""


def test_check_non_semver_tag_is_report_only(tmp_path: Path) -> None:
    link = _make_link(tmp_path)
    with patch(
        "repo_ops_upgrade.submodules._current_submodule_tag", return_value="main"
    ):
        bumps = check_submodule_tag_advances([link])
    assert len(bumps) == 1
    assert not bumps[0].is_actionable
    assert "non-semver" in bumps[0].latest_tag
    assert bumps[0].current_tag == "main"


def test_check_network_failure_is_report_only(tmp_path: Path) -> None:
    link = _make_link(tmp_path)
    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.0.0"
        ),
        patch("repo_ops_upgrade.submodules._fetch_remote_tags", return_value=None),
    ):
        bumps = check_submodule_tag_advances([link])
    assert len(bumps) == 1
    assert not bumps[0].is_actionable
    assert "fetch failed" in bumps[0].latest_tag


def test_check_already_at_latest_omitted(tmp_path: Path) -> None:
    link = _make_link(tmp_path)
    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.2.3"
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.2.2", "v1.2.3"],
        ),
    ):
        bumps = check_submodule_tag_advances([link])
    assert bumps == []


def test_check_actionable_bump_detected(tmp_path: Path) -> None:
    link = _make_link(tmp_path)
    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.0.0"
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.0.0", "v1.1.0", "v1.2.0"],
        ),
    ):
        bumps = check_submodule_tag_advances([link])
    assert len(bumps) == 1
    b = bumps[0]
    assert b.is_actionable
    assert b.current_tag == "v1.0.0"
    assert b.latest_tag == "v1.2.0"
    assert b.path == "sub"
    assert b.url == "https://example.com/sub.git"


def test_check_cross_major_blocked_by_default(tmp_path: Path) -> None:
    link = _make_link(tmp_path)
    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.2.3"
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.2.3", "v2.0.0"],
        ),
    ):
        bumps = check_submodule_tag_advances([link])
    # v2.0.0 is cross-major; default conservative mode → no bump
    assert bumps == []


def test_check_cross_major_enabled_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_SUBMODULE_UPDATE_LATEST", "1")
    link = _make_link(tmp_path)
    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.2.3"
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.2.3", "v2.0.0"],
        ),
    ):
        bumps = check_submodule_tag_advances([link])
    assert len(bumps) == 1
    assert bumps[0].latest_tag == "v2.0.0"


def test_check_multiple_submodules(tmp_path: Path) -> None:
    """Mixed bag: one actionable, one SHA-only, one already at latest."""
    links = [
        _make_link(tmp_path, path="sub-a", url="https://example.com/a.git"),
        _make_link(tmp_path, path="sub-b", url="https://example.com/b.git"),
        _make_link(tmp_path, path="sub-c", url="https://example.com/c.git"),
    ]

    def _fake_current_tag(git_repo: Path) -> str | None:
        name = git_repo.name
        if name == "sub-a":
            return "v1.0.0"  # actionable
        if name == "sub-b":
            return None  # SHA-only
        return "v2.5.0"  # sub-c: already at latest

    def _fake_fetch(url: str) -> list[str] | None:
        if "a.git" in url:
            return ["v1.0.0", "v1.1.0"]
        if "b.git" in url:
            return ["v1.0.0"]
        return ["v2.5.0"]  # sub-c

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag",
            side_effect=_fake_current_tag,
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags", side_effect=_fake_fetch
        ),
    ):
        bumps = check_submodule_tag_advances(links)

    actionable = [b for b in bumps if b.is_actionable]
    report_only = [b for b in bumps if not b.is_actionable]
    assert len(actionable) == 1
    assert actionable[0].path == "sub-a"
    assert actionable[0].latest_tag == "v1.1.0"
    assert len(report_only) == 1
    assert report_only[0].path == "sub-b"


# ---------------------------------------------------------------------------
# apply_submodule_tag_advances
# ---------------------------------------------------------------------------


def test_apply_calls_fetch_checkout_add_for_actionable(tmp_path: Path) -> None:
    """Apply issues fetch → checkout → add for each actionable bump."""
    link = _make_link(tmp_path)

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.0.0"
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.0.0", "v1.1.0"],
        ),
        patch(
            "repo_ops_upgrade.submodules.run_git", return_value=_ok_proc()
        ) as mock_git,
    ):
        bumps = apply_submodule_tag_advances([link])

    assert len(bumps) == 1
    assert bumps[0].is_actionable

    # Expect exactly three run_git calls: fetch, checkout, add
    assert mock_git.call_count == 3
    calls = mock_git.call_args_list
    # fetch: second arg is "fetch"
    assert calls[0][0][1] == "fetch"
    # checkout: now uses --detach refs/tags/<tag> for unambiguous resolution
    assert calls[1][0][1] == "checkout"
    assert calls[1][0][2] == "--detach"
    assert calls[1][0][3] == "refs/tags/v1.1.0"
    # add: called on the parent
    assert calls[2][0][1] == "add"
    assert calls[2][0][2] == "sub"


def test_apply_skips_git_ops_for_report_only(tmp_path: Path) -> None:
    """No fetch/checkout/add calls for non-actionable (SHA-only) bumps."""
    link = _make_link(tmp_path)

    with (
        patch("repo_ops_upgrade.submodules._current_submodule_tag", return_value=None),
        patch(
            "repo_ops_upgrade.submodules.run_git", return_value=_ok_proc()
        ) as mock_git,
    ):
        bumps = apply_submodule_tag_advances([link])

    assert len(bumps) == 1
    assert not bumps[0].is_actionable
    # run_git must NOT be called for the apply ops (no fetch/checkout/add)
    mock_git.assert_not_called()


def test_apply_continues_on_fetch_failure(tmp_path: Path) -> None:
    """A failing fetch for one submodule does not abort others."""
    link_a = _make_link(tmp_path, path="sub-a", url="https://example.com/a.git")
    link_b = _make_link(tmp_path, path="sub-b", url="https://example.com/b.git")

    def _fake_current(git_repo: Path) -> str:
        return "v1.0.0"

    def _fake_fetch(url: str) -> list[str]:
        return ["v1.0.0", "v1.1.0"]

    fetch_fail = _fail_proc()
    ok = _ok_proc()

    call_seq = iter([fetch_fail, ok, ok, ok, ok])

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag",
            side_effect=_fake_current,
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags", side_effect=_fake_fetch
        ),
        patch("repo_ops_upgrade.submodules.run_git", side_effect=call_seq),
    ):
        bumps = apply_submodule_tag_advances([link_a, link_b])

    assert len([b for b in bumps if b.is_actionable]) == 2


# ---------------------------------------------------------------------------
# Integration: SubmoduleLink.url populated by discovery
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_discover_populates_url(tmp_path: Path) -> None:
    """discover_submodules_under_roots stores the URL from .gitmodules."""
    parent = tmp_path / "umbrella"
    child = parent / "libs" / "child"
    child.mkdir(parents=True)
    _init_git_repo(parent)
    _init_git_repo(child)
    (parent / ".gitmodules").write_text(
        '[submodule "libs/child"]\n'
        "\tpath = libs/child\n"
        "\turl = https://example.com/child.git\n",
        encoding="utf-8",
    )

    links = discover_submodules_under_roots([str(parent)], flat=True)
    assert len(links) == 1
    assert links[0].url == "https://example.com/child.git"


def test_submodule_tag_bump_to_json(tmp_path: Path) -> None:
    """SubmoduleTagBump.to_json includes all expected fields."""
    bump = SubmoduleTagBump(
        path="sub",
        url="https://example.com/sub.git",
        current_tag="v1.0.0",
        latest_tag="v1.1.0",
        parent=tmp_path,
        git_repo=tmp_path / "sub",
    )
    j = bump.to_json()
    assert j["path"] == "sub"
    assert j["url"] == "https://example.com/sub.git"
    assert j["current_tag"] == "v1.0.0"
    assert j["latest_tag"] == "v1.1.0"
    assert j["actionable"] == "true"
