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
from unittest.mock import patch

import pytest

from repo_ops_core.cli import build_upgrade_parser
from repo_ops_models import Workspace
from repo_ops_upgrade.precommit import (
    _clear_tag_cache,
    _fetch_tags_for_repo,
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


def _fake_ls_remote(tags: list[str]) -> subprocess.CompletedProcess[str]:
    """Return a side-effect for subprocess.run that simulates git ls-remote."""
    fake_sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    stdout = "\n".join(f"{fake_sha}\trefs/tags/{t}" for t in tags) + "\n"
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def _fake_ls_remote_refs(
    refs: list[tuple[str, str]],
) -> subprocess.CompletedProcess[str]:
    """Return a fake ``git ls-remote`` result with tag commit SHAs."""
    stdout = "\n".join(f"{sha}\trefs/tags/{tag}" for sha, tag in refs) + "\n"
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


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


# ---------------------------------------------------------------------------
# _resolve_latest_precommit_rev
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "tags", "allow_cross_major", "expected"),
    [
        ("v4.5.0", ["v4.5.0", "v4.6.0", "v4.6.1", "v5.0.0"], False, "v4.6.1"),
        # Without allow_cross_major, v5 should not be picked.
        ("v4.5.0", ["v4.5.0", "v4.6.1", "v5.0.0"], False, "v4.6.1"),
        ("v4.5.0", ["v4.5.0", "v4.6.1", "v5.0.0"], True, "v5.0.0"),
        # Bare (non-v-prefixed) revs match bare tags only.
        ("23.3.0", ["23.3.0", "23.4.0", "v24.0.0"], False, "23.4.0"),
        ("v4.6.1", ["v4.5.0", "v4.6.1"], False, None),
        # SHA or branch rev → None (non-actionable).
        ("abc1234", ["v4.5.0"], False, None),
        ("main", ["v4.5.0"], False, None),
        ("v4.5.0", [], False, None),
        # current is v-prefixed but tags are bare → no match.
        ("v4.5.0", ["4.5.0", "4.6.0"], False, None),
    ],
)
def test_resolve_latest_precommit_rev(
    current: str, tags: list[str], allow_cross_major: bool, expected: str | None
) -> None:
    result = _resolve_latest_precommit_rev(current, tags, allow_cross_major=allow_cross_major)
    assert result == expected


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
    proc = subprocess.CompletedProcess([], 128, stdout="", stderr="")
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


def test_check_precommit_sha_pin_refreshes_with_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", "1")
    current_sha = "a" * 40
    latest_sha = "b" * 40
    (tmp_path / ".pre-commit-config.yaml").write_text(
        f"repos:\n- repo: https://github.com/org/tool\n  rev: {current_sha}\n  hooks:\n  - id: x\n"
    )
    proc = _fake_ls_remote_refs(
        [
            (current_sha, "v1.0.0"),
            (latest_sha, "v1.1.0"),
            ("c" * 40, "v2.0.0"),
        ]
    )
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].from_ver == current_sha
    assert changes[0].to == latest_sha
    assert "[report-only]" not in changes[0].to


def test_check_precommit_sha_refresh_stays_within_major_under_update_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", "1")
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_UPDATE_LATEST", "1")
    current_sha = "a" * 40
    latest_sha = "b" * 40
    cross_major_sha = "c" * 40
    (tmp_path / ".pre-commit-config.yaml").write_text(
        f"repos:\n- repo: https://github.com/org/tool\n  rev: {current_sha}\n  hooks:\n  - id: x\n"
    )
    proc = _fake_ls_remote_refs(
        [
            (current_sha, "v1.0.0"),
            (latest_sha, "v1.1.0"),
            (cross_major_sha, "v2.0.0"),
        ]
    )
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].to == latest_sha
    assert changes[0].to != cross_major_sha


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


def test_check_precommit_cross_major_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_UPDATE_LATEST", "1")
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/org/tool\n  rev: v1.9.0\n  hooks:\n  - id: x\n"
    )
    proc = _fake_ls_remote(["v1.9.0", "v2.0.0"])
    with patch("subprocess.run", return_value=proc):
        changes = check_precommit(_make_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].to == "v2.0.0"


def test_precommit_and_submodule_refresh_sha_flags_removed() -> None:
    """#136: --precommit-refresh-sha/--submodule-refresh-sha no longer exist as
    parse-time argparse flags — refresh-sha behavior is now part of the default
    full sweep in run.py, opt-out via --conservative (env-var wiring covered by
    tests/test_run_targeted_bump_getattr.py)."""
    parser = build_upgrade_parser(apply=False)
    with pytest.raises(SystemExit):
        parser.parse_args(["--precommit-refresh-sha"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--submodule-refresh-sha"])


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
    original = "repos:\n- repo: https://github.com/org/tool\n  rev: 'v1.0.0'\n  hooks:\n  - id: x\n"
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    proc = _fake_ls_remote(["v1.0.0", "v1.1.0"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    result = cfg.read_text(encoding="utf-8")
    assert "rev: 'v1.1.0'" in result


def test_apply_precommit_no_change_when_at_latest(tmp_path: Path) -> None:
    original = "repos:\n- repo: https://github.com/org/tool\n  rev: v2.0.0\n  hooks:\n  - id: x\n"
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    proc = _fake_ls_remote(["v2.0.0"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    assert cfg.read_text(encoding="utf-8") == original


def test_apply_precommit_sha_pin_not_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Digest refresh defaults on with the sweep (#136); pin it off so this
    # test exercises the plain SHA-pin-untouched path.
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", "0")
    sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    original = f"repos:\n- repo: https://github.com/org/tool\n  rev: {sha}\n  hooks:\n  - id: x\n"
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    # No subprocess call expected (SHA is report-only)
    with patch("subprocess.run") as mock_run:
        apply_precommit(_make_ws(tmp_path))

    mock_run.assert_not_called()
    assert cfg.read_text(encoding="utf-8") == original


def test_apply_precommit_refreshes_sha_pin_with_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", "1")
    current_sha = "a" * 40
    latest_sha = "b" * 40
    original = (
        f"repos:\n- repo: https://github.com/org/tool\n  rev: {current_sha}\n  hooks:\n  - id: x\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")
    proc = _fake_ls_remote_refs(
        [(current_sha, "v1.0.0"), (latest_sha, "v1.1.0"), ("c" * 40, "v2.0.0")]
    )
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))
    result = cfg.read_text(encoding="utf-8")
    assert f"rev: {latest_sha}" in result
    assert f"rev: {current_sha}" not in result


# ---------------------------------------------------------------------------
# Robustness bug fixes (rev-collision, cache lock, non-UTF-8 round-trip)
# ---------------------------------------------------------------------------


def test_apply_precommit_rev_collision_two_repos_get_own_target(tmp_path: Path) -> None:
    """Bug 1 fix: two repos pinned at the SAME current rev but with DIFFERENT
    latest tags must each receive their own correct target — not the same
    last-write-wins value that the old from_ver-keyed dict produced."""
    original = (
        "repos:\n"
        "- repo: https://github.com/org/repo-a\n"
        "  rev: v1.0.0\n"
        "  hooks:\n"
        "  - id: hook-a\n"
        "- repo: https://github.com/org/repo-b\n"
        "  rev: v1.0.0\n"
        "  hooks:\n"
        "  - id: hook-b\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(original, encoding="utf-8")

    def fake_run(cmd, **kwargs):
        # cmd[-1] is the repo URL
        url = cmd[-1]
        if "repo-a" in url:
            return _fake_ls_remote(["v1.0.0", "v1.1.0"])
        return _fake_ls_remote(["v1.0.0", "v1.2.0"])

    with patch("subprocess.run", side_effect=fake_run):
        apply_precommit(_make_ws(tmp_path))

    result = cfg.read_text(encoding="utf-8")
    rev_lines = [ln.strip() for ln in result.splitlines() if ln.strip().startswith("rev:")]
    assert len(rev_lines) == 2
    # Each repo must receive its own distinct bump, not the same value.
    assert "rev: v1.1.0" in rev_lines, f"repo-a target missing from: {rev_lines}"
    assert "rev: v1.2.0" in rev_lines, f"repo-b target missing from: {rev_lines}"


def test_fetch_tags_cache_lock_deduplicates_concurrent() -> None:
    """Bug 2 fix: concurrent workers for the same repo URL invoke ls-remote exactly
    once — the threading.Lock prevents cache stampede and duplicate subprocess calls."""
    from concurrent.futures import ThreadPoolExecutor

    proc = _fake_ls_remote(["v1.0.0"])
    with patch("subprocess.run", return_value=proc) as mock_run:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [
                ex.submit(_fetch_tags_for_repo, "https://github.com/org/repo") for _ in range(4)
            ]
        results = [f.result() for f in futures]

    assert mock_run.call_count == 1, (
        f"ls-remote called {mock_run.call_count} times; expected 1 (cache lock broken)"
    )
    assert all(r == ["v1.0.0"] for r in results)


def test_apply_precommit_non_utf8_bytes_round_trip(tmp_path: Path) -> None:
    """Bug 3 fix: a config containing non-UTF-8 bytes in a comment must survive
    apply unchanged — the 0xFF byte must be present in the output and the
    unrelated rev: line must still be updated correctly."""
    # Embed a raw 0xFF byte in a comment — invalid in UTF-8.
    original_bytes = (
        b"# comment with non-utf8 byte: \xff\n"
        b"repos:\n"
        b"- repo: https://github.com/org/tool\n"
        b"  rev: v1.0.0\n"
        b"  hooks:\n"
        b"  - id: x\n"
    )
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_bytes(original_bytes)

    proc = _fake_ls_remote(["v1.0.0", "v1.1.0"])
    with patch("subprocess.run", return_value=proc):
        apply_precommit(_make_ws(tmp_path))

    result_bytes = cfg.read_bytes()
    # Non-UTF-8 byte must be preserved verbatim.
    assert b"\xff" in result_bytes, "non-UTF-8 byte was corrupted on writeback"
    # Rev must be updated.
    assert b"rev: v1.1.0" in result_bytes
    # Full comment line must be intact.
    assert b"# comment with non-utf8 byte: \xff" in result_bytes


# ---------------------------------------------------------------------------
# _register.py helper
# ---------------------------------------------------------------------------


def test_register_precommit_registers_with_upgrade_registry() -> None:
    """register_precommit() adds 'precommit' to the upgrade registry."""
    from repo_ops._register import register_precommit
    from repo_ops_upgrade import registry as upgrade_registry

    # Remove any pre-existing registration to make the test idempotent.
    upgrade_registry.CHECKERS.pop("precommit", None)
    upgrade_registry.APPLIERS.pop("precommit", None)

    register_precommit()

    assert "precommit" in upgrade_registry.CHECKERS
    assert "precommit" in upgrade_registry.APPLIERS
