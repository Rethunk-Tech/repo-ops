"""Tests for pre-commit rev: pin detection, plan, and apply (sub-task 8c).

Covers:
  * is_precommit_workspace: detects .pre-commit-config.yaml / .yml
  * _parse_precommit_repos: 2-space, 4-space, quoted revs, multiple repos,
    no-rev repo skipped
  * _is_sha_rev / _is_semver_like / _has_v_prefix / _resolve_latest_precommit_rev
  * _fetch_tags_for_repo: happy path, git not found, timeout, non-zero exit
  * check_precommit: actionable bump, SHA pin, non-semver rev, network error,
    already-at-latest, cross-major opt-in via env var
  * apply_precommit: byte-preserving rev replacement, CRLF safe, quoted revs

No real subprocesses or network calls are made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_models import Workspace
from repo_ops_upgrade.precommit import (
    _clear_tag_cache,
    _fetch_tags_for_repo,
    _has_v_prefix,
    _is_semver_like,
    _is_sha_rev,
    _parse_precommit_repos,
    _resolve_latest_precommit_rev,
    apply_precommit,
    check_precommit,
    is_precommit_workspace,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_FIXTURE_CONFIG = """\
repos:
-   repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
    -   id: trailing-whitespace
-   repo: https://github.com/psf/black
    rev: 23.3.0
    hooks:
    -   id: black
-   repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.6
    hooks:
    -   id: ruff
"""

# Repo with quoted rev and 2-space indent
_FIXTURE_QUOTED_REV = """\
repos:
- repo: https://github.com/example/tool
  rev: 'v1.2.3'
  hooks:
  - id: my-hook
"""

# Repo with double-quoted rev
_FIXTURE_DOUBLE_QUOTED = """\
repos:
- repo: https://github.com/example/tool
  rev: "v1.2.3"
  hooks:
  - id: my-hook
"""

# Repo missing a rev: key entirely
_FIXTURE_NO_REV = """\
repos:
- repo: local
  hooks:
  - id: my-local-hook
    name: local hook
    entry: ./scripts/lint.sh
    language: script
"""

# Mixed: one with rev, one without
_FIXTURE_MIXED = """\
repos:
- repo: local
  hooks:
  - id: local-hook
- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: v4.6.0
  hooks:
  - id: trailing-whitespace
"""


def _make_ws(path: Path) -> Workspace:
    return Workspace(
        path=path,
        ecosystem="precommit",
        manager="precommit",
        git_repo=path,
        label="",
    )


def _fake_ls_remote(tags: list[str]):
    """Return a side-effect for subprocess.run that simulates git ls-remote."""
    fake_sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    stdout = "\n".join(f"{fake_sha}\trefs/tags/{t}" for t in tags) + "\n"
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = stdout
    return proc


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the in-process tag cache before each test."""
    _clear_tag_cache()
    yield
    _clear_tag_cache()


# ---------------------------------------------------------------------------
# is_precommit_workspace
# ---------------------------------------------------------------------------


def test_is_precommit_workspace_yaml(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(_FIXTURE_CONFIG)
    assert is_precommit_workspace(tmp_path) is True


def test_is_precommit_workspace_yml(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yml").write_text(_FIXTURE_CONFIG)
    assert is_precommit_workspace(tmp_path) is True


def test_is_precommit_workspace_false_when_absent(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# test")
    assert is_precommit_workspace(tmp_path) is False


def test_is_precommit_workspace_false_empty_dir(tmp_path: Path) -> None:
    assert is_precommit_workspace(tmp_path) is False


# ---------------------------------------------------------------------------
# _parse_precommit_repos
# ---------------------------------------------------------------------------


def test_parse_repos_standard_fixture() -> None:
    repos = _parse_precommit_repos(_FIXTURE_CONFIG)
    assert len(repos) == 3
    assert repos[0]["repo"] == "https://github.com/pre-commit/pre-commit-hooks"
    assert repos[0]["rev"] == "v4.5.0"
    assert repos[1]["repo"] == "https://github.com/psf/black"
    assert repos[1]["rev"] == "23.3.0"
    assert repos[2]["rev"] == "v0.1.6"


def test_parse_repos_quoted_single(tmp_path: Path) -> None:
    repos = _parse_precommit_repos(_FIXTURE_QUOTED_REV)
    assert repos[0]["rev"] == "v1.2.3"  # quotes stripped


def test_parse_repos_quoted_double() -> None:
    repos = _parse_precommit_repos(_FIXTURE_DOUBLE_QUOTED)
    assert repos[0]["rev"] == "v1.2.3"


def test_parse_repos_skips_no_rev() -> None:
    repos = _parse_precommit_repos(_FIXTURE_NO_REV)
    assert repos == []


def test_parse_repos_mixed_local_and_remote() -> None:
    repos = _parse_precommit_repos(_FIXTURE_MIXED)
    # Only the remote repo with a rev should appear
    assert len(repos) == 1
    assert repos[0]["rev"] == "v4.6.0"


def test_parse_repos_empty_string() -> None:
    assert _parse_precommit_repos("") == []


def test_parse_repos_rev_line_no_is_1indexed() -> None:
    repos = _parse_precommit_repos(_FIXTURE_CONFIG)
    # rev line numbers should be positive integers
    for r in repos:
        assert isinstance(r["rev_line_no"], int)
        assert r["rev_line_no"] > 0


# ---------------------------------------------------------------------------
# Rev classification helpers
# ---------------------------------------------------------------------------


def test_is_sha_rev_40_hex() -> None:
    assert _is_sha_rev("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef") is True


def test_is_sha_rev_short_sha() -> None:
    assert _is_sha_rev("abc1234") is True


def test_is_sha_rev_tag_not_sha() -> None:
    assert _is_sha_rev("v4.5.0") is False
    assert _is_sha_rev("23.3.0") is False


def test_is_semver_like_v_prefix() -> None:
    assert _is_semver_like("v4.5.0") is True


def test_is_semver_like_bare() -> None:
    assert _is_semver_like("23.3.0") is True


def test_is_semver_like_branch_names() -> None:
    assert _is_semver_like("main") is False
    assert _is_semver_like("master") is False


def test_has_v_prefix() -> None:
    assert _has_v_prefix("v1.0.0") is True
    assert _has_v_prefix("1.0.0") is False


# ---------------------------------------------------------------------------
# _resolve_latest_precommit_rev
# ---------------------------------------------------------------------------


def test_resolve_latest_same_major() -> None:
    tags = ["v4.5.0", "v4.6.0", "v4.6.1", "v5.0.0"]
    assert _resolve_latest_precommit_rev("v4.5.0", tags) == "v4.6.1"


def test_resolve_latest_cross_major_off_by_default() -> None:
    tags = ["v4.5.0", "v4.6.1", "v5.0.0"]
    # Without allow_cross_major, v5 should not be picked
    result = _resolve_latest_precommit_rev("v4.5.0", tags)
    assert result == "v4.6.1"
    assert result != "v5.0.0"


def test_resolve_latest_cross_major_opt_in() -> None:
    tags = ["v4.5.0", "v4.6.1", "v5.0.0"]
    result = _resolve_latest_precommit_rev("v4.5.0", tags, allow_cross_major=True)
    assert result == "v5.0.0"


def test_resolve_latest_bare_semver() -> None:
    """Bare (non-v-prefixed) revs match bare tags only."""
    tags = ["23.3.0", "23.4.0", "v24.0.0"]
    result = _resolve_latest_precommit_rev("23.3.0", tags)
    assert result == "23.4.0"


def test_resolve_latest_already_at_latest() -> None:
    tags = ["v4.5.0", "v4.6.1"]
    assert _resolve_latest_precommit_rev("v4.6.1", tags) is None


def test_resolve_latest_non_semver_returns_none() -> None:
    """SHA or branch rev → None (non-actionable)."""
    tags = ["v4.5.0"]
    assert _resolve_latest_precommit_rev("abc1234", tags) is None
    assert _resolve_latest_precommit_rev("main", tags) is None


def test_resolve_latest_no_candidates() -> None:
    assert _resolve_latest_precommit_rev("v4.5.0", []) is None


def test_resolve_latest_prefix_mismatch_excluded() -> None:
    """v-prefixed rev must not match bare tags."""
    tags = ["4.5.0", "4.6.0"]
    # current is v-prefixed but tags are bare → no match
    assert _resolve_latest_precommit_rev("v4.5.0", tags) is None


# ---------------------------------------------------------------------------
# _fetch_tags_for_repo
# ---------------------------------------------------------------------------


def test_fetch_tags_happy_path() -> None:
    proc = _fake_ls_remote(["v4.5.0", "v4.6.0", "v4.6.1"])
    with patch("subprocess.run", return_value=proc):
        tags = _fetch_tags_for_repo("https://github.com/org/repo")
    assert "v4.5.0" in tags
    assert "v4.6.1" in tags


def test_fetch_tags_git_not_found() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _fetch_tags_for_repo("https://github.com/org/repo") is None


def test_fetch_tags_timeout() -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=30)):
        assert _fetch_tags_for_repo("https://github.com/org/repo") is None


def test_fetch_tags_nonzero_exit() -> None:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 128
    proc.stdout = ""
    with patch("subprocess.run", return_value=proc):
        assert _fetch_tags_for_repo("https://github.com/org/repo") is None


def test_fetch_tags_cached_second_call_no_subprocess() -> None:
    proc = _fake_ls_remote(["v1.0.0"])
    with patch("subprocess.run", return_value=proc) as mock_run:
        _fetch_tags_for_repo("https://github.com/org/repo")
        _fetch_tags_for_repo("https://github.com/org/repo")
    assert mock_run.call_count == 1  # second call served from cache


# ---------------------------------------------------------------------------
# check_precommit
# ---------------------------------------------------------------------------


def test_check_precommit_actionable_bump(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/pre-commit/pre-commit-hooks\n  rev: v4.5.0\n  hooks:\n  - id: trailing-whitespace\n"
    )
    available = ["v4.5.0", "v4.6.0", "v4.6.1"]
    proc = _fake_ls_remote(available)
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].from_ver == "v4.5.0"
    assert changes[0].to == "v4.6.1"
    assert "[report-only]" not in changes[0].to


def test_check_precommit_sha_pin_report_only(tmp_path: Path) -> None:
    sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    (tmp_path / ".pre-commit-config.yaml").write_text(
        f"repos:\n- repo: https://github.com/org/tool\n  rev: {sha}\n  hooks:\n  - id: x\n"
    )
    changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert "[report-only]" in changes[0].to
    assert "SHA pin" in changes[0].to


def test_check_precommit_non_semver_rev_report_only(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/org/tool\n  rev: main\n  hooks:\n  - id: x\n"
    )
    changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert "[report-only]" in changes[0].to


def test_check_precommit_network_failure_report_only(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/org/tool\n  rev: v1.0.0\n  hooks:\n  - id: x\n"
    )
    with patch("subprocess.run", side_effect=FileNotFoundError):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert "[report-only]" in changes[0].to


def test_check_precommit_already_at_latest_no_change(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/org/tool\n  rev: v2.0.0\n  hooks:\n  - id: x\n"
    )
    proc = _fake_ls_remote(["v2.0.0", "v1.9.0"])
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert changes == []


def test_check_precommit_no_config_no_changes(tmp_path: Path) -> None:
    changes = check_precommit(_make_ws(tmp_path))
    assert changes == []


def test_check_precommit_cross_major_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_UPDATE_LATEST", "1")
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/org/tool\n  rev: v1.9.0\n  hooks:\n  - id: x\n"
    )
    proc = _fake_ls_remote(["v1.9.0", "v2.0.0"])
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].to == "v2.0.0"


def test_check_precommit_deduplicates_same_repo(tmp_path: Path) -> None:
    """Same repo URL appearing twice → only one Change (or none if up-to-date)."""
    text = (
        "repos:\n"
        "- repo: https://github.com/org/tool\n  rev: v1.0.0\n  hooks:\n  - id: a\n"
        "- repo: https://github.com/org/tool\n  rev: v1.0.0\n  hooks:\n  - id: b\n"
    )
    (tmp_path / ".pre-commit-config.yaml").write_text(text)
    proc = _fake_ls_remote(["v1.0.0", "v1.1.0"])
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1


# ---------------------------------------------------------------------------
# apply_precommit
# ---------------------------------------------------------------------------


def test_apply_precommit_updates_rev_in_place(tmp_path: Path) -> None:
    original = (
        "repos:\n"
        "- repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "  rev: v4.5.0\n"
        "  hooks:\n"
        "  - id: trailing-whitespace\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    proc = _fake_ls_remote(["v4.5.0", "v4.6.0", "v4.6.1"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    result = cfg.read_text(encoding="utf-8")
    assert "rev: v4.6.1" in result
    assert "rev: v4.5.0" not in result
    # Other lines untouched
    assert "pre-commit-hooks" in result
    assert "trailing-whitespace" in result


def test_apply_precommit_preserves_crlf(tmp_path: Path) -> None:
    original = (
        "repos:\r\n"
        "- repo: https://github.com/org/tool\r\n"
        "  rev: v1.0.0\r\n"
        "  hooks:\r\n"
        "  - id: x\r\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_bytes(original.encode("utf-8"))

    proc = _fake_ls_remote(["v1.0.0", "v1.1.0"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    content = cfg.read_bytes()
    assert b"rev: v1.1.0\r\n" in content


def test_apply_precommit_preserves_single_quote(tmp_path: Path) -> None:
    original = (
        "repos:\n"
        "- repo: https://github.com/org/tool\n"
        "  rev: 'v1.0.0'\n"
        "  hooks:\n"
        "  - id: x\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    proc = _fake_ls_remote(["v1.0.0", "v1.1.0"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    result = cfg.read_text(encoding="utf-8")
    assert "rev: 'v1.1.0'" in result


def test_apply_precommit_no_change_when_at_latest(tmp_path: Path) -> None:
    original = (
        "repos:\n"
        "- repo: https://github.com/org/tool\n"
        "  rev: v2.0.0\n"
        "  hooks:\n"
        "  - id: x\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    proc = _fake_ls_remote(["v2.0.0"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    assert cfg.read_text(encoding="utf-8") == original


def test_apply_precommit_sha_pin_not_modified(tmp_path: Path) -> None:
    sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    original = (
        f"repos:\n"
        f"- repo: https://github.com/org/tool\n"
        f"  rev: {sha}\n"
        f"  hooks:\n"
        f"  - id: x\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    # No subprocess call expected (SHA is report-only)
    with patch("subprocess.run") as mock_run:
        apply_precommit(_make_ws(tmp_path))

    mock_run.assert_not_called()
    assert cfg.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# _register.py helper
# ---------------------------------------------------------------------------


def test_register_precommit_registers_with_upgrade_registry() -> None:
    """register_precommit() adds 'precommit' to the upgrade registry."""
    from repo_ops_core._register import register_precommit
    from repo_ops_upgrade import registry as upgrade_registry

    # Remove any pre-existing registration to make the test idempotent.
    upgrade_registry.CHECKERS.pop("precommit", None)
    upgrade_registry.APPLIERS.pop("precommit", None)

    register_precommit()

    assert "precommit" in upgrade_registry.CHECKERS
    assert "precommit" in upgrade_registry.APPLIERS
