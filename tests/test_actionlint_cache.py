"""Tests for the actionlint content-hash result cache (issue #123)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_upgrade.actions import (
    _LINT_CACHE_MAX_AGE_S,
    _actionlint_cache_path,
    _lint_cache_key,
    _load_lint_cache,
    _run_actionlint,
    _save_lint_cache,
    scan_actions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_WF = """\
name: CI
on: push
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def _wf(tmp_path: Path, content: str = _SIMPLE_WF) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    p = wf_dir / "ci.yml"
    p.write_text(content, encoding="utf-8")
    return p


def _actionlint_pass(*_args: object, **_kwargs: object) -> MagicMock:
    return MagicMock(returncode=0, stdout="", stderr="")


def _actionlint_fail(*_args: object, **_kwargs: object) -> MagicMock:
    return MagicMock(returncode=1, stdout="", stderr="lint error text")


# ---------------------------------------------------------------------------
# _actionlint_cache_path
# ---------------------------------------------------------------------------


def test_cache_path_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default path uses ~/.cache/repo-ops/actionlint-cache.json."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _actionlint_cache_path()
    assert p == tmp_path / ".cache" / "repo-ops" / "actionlint-cache.json"


def test_cache_path_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """XDG_CACHE_HOME overrides the default base."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    p = _actionlint_cache_path()
    assert p == tmp_path / "xdg" / "repo-ops" / "actionlint-cache.json"


# ---------------------------------------------------------------------------
# _load_lint_cache / _save_lint_cache
# ---------------------------------------------------------------------------


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    assert _load_lint_cache(tmp_path / "nonexistent.json") == {}


def test_load_corrupt_returns_empty(tmp_path: Path) -> None:
    bad = tmp_path / "corrupt.json"
    bad.write_text("not json{{}", encoding="utf-8")
    assert _load_lint_cache(bad) == {}


def test_load_non_dict_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert _load_lint_cache(p) == {}


def test_save_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "repo-ops" / "actionlint-cache.json"
    cache = {"abc123": {"result": "error text", "ts": time.time()}}
    _save_lint_cache(path, cache)
    loaded = _load_lint_cache(path)
    assert loaded == cache


def test_save_prunes_old_entries(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    old_ts = time.time() - _LINT_CACHE_MAX_AGE_S - 1
    fresh_ts = time.time()
    cache: dict[str, dict] = {
        "old_key": {"result": None, "ts": old_ts},
        "fresh_key": {"result": "err", "ts": fresh_ts},
    }
    _save_lint_cache(path, cache)
    loaded = _load_lint_cache(path)
    assert "old_key" not in loaded
    assert "fresh_key" in loaded


# ---------------------------------------------------------------------------
# _lint_cache_key
# ---------------------------------------------------------------------------


def test_cache_key_changes_on_content(tmp_path: Path) -> None:
    k1 = _lint_cache_key(b"content-a", "v1.0")
    k2 = _lint_cache_key(b"content-b", "v1.0")
    assert k1 != k2


def test_cache_key_changes_on_version(tmp_path: Path) -> None:
    k1 = _lint_cache_key(b"same", "v1.0")
    k2 = _lint_cache_key(b"same", "v2.0")
    assert k1 != k2


def test_cache_key_stable(tmp_path: Path) -> None:
    k1 = _lint_cache_key(b"data", "v1.0")
    k2 = _lint_cache_key(b"data", "v1.0")
    assert k1 == k2


# ---------------------------------------------------------------------------
# _run_actionlint: cache hit avoids subprocess
# ---------------------------------------------------------------------------


def test_cache_hit_skips_subprocess(tmp_path: Path) -> None:
    """A warm cache entry must be returned without invoking the subprocess."""
    wf = _wf(tmp_path)
    wf_bytes = wf.read_bytes()
    version = "1.0.0"
    key = _lint_cache_key(wf_bytes, version)
    lint_cache: dict = {key: {"result": "cached error", "ts": time.time()}}

    subprocess_called = []

    def fake_run(*_args: object, **_kwargs: object) -> MagicMock:  # pragma: no cover
        subprocess_called.append(True)
        return _actionlint_pass()

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/actionlint"
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value=version),
    ):
        result = _run_actionlint(wf, tmp_path, lint_cache=lint_cache)

    # Old cache entries without a "severity" key default to "error".
    assert result == ("cached error", "error")
    assert not subprocess_called, "subprocess must not be called on cache hit"


def test_cache_miss_runs_subprocess_and_stores(tmp_path: Path) -> None:
    """A cold cache must run the subprocess and populate the cache dict."""
    wf = _wf(tmp_path)
    version = "1.0.0"
    lint_cache: dict = {}

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/actionlint"
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=_actionlint_fail),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value=version),
    ):
        result = _run_actionlint(wf, tmp_path, lint_cache=lint_cache)

    assert result == ("lint error text", "error")
    # Cache must now contain an entry for this file.
    assert len(lint_cache) == 1
    entry = next(iter(lint_cache.values()))
    assert entry["result"] == "lint error text"
    assert entry["severity"] == "error"


def test_cache_pass_stored_as_none(tmp_path: Path) -> None:
    """A passing lint result is stored as None in the cache."""
    wf = _wf(tmp_path)
    version = "1.0.0"
    lint_cache: dict = {}

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/actionlint"
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=_actionlint_pass),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value=version),
    ):
        result = _run_actionlint(wf, tmp_path, lint_cache=lint_cache)

    assert result is None
    entry = next(iter(lint_cache.values()))
    assert entry["result"] is None


# ---------------------------------------------------------------------------
# Content change causes cache miss
# ---------------------------------------------------------------------------


def test_content_change_is_cache_miss(tmp_path: Path) -> None:
    """Changing the workflow file content must produce a different cache key."""
    wf = _wf(tmp_path, "name: A\njobs: {}\n")
    version = "1.0.0"
    lint_cache: dict = {}

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/actionlint"
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=_actionlint_pass),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value=version),
    ):
        _run_actionlint(wf, tmp_path, lint_cache=lint_cache)

    first_key = next(iter(lint_cache))

    # Mutate the file content.
    wf.write_text("name: B\njobs: {}\n", encoding="utf-8")

    call_count = [0]

    def fake_run_counter(*_: object, **__: object) -> MagicMock:
        call_count[0] += 1
        return _actionlint_pass()

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/actionlint"
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run_counter),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value=version),
    ):
        _run_actionlint(wf, tmp_path, lint_cache=lint_cache)

    assert call_count[0] == 1, "subprocess must run again after content change"
    assert len(lint_cache) == 2
    second_key = next(k for k in lint_cache if k != first_key)
    assert first_key != second_key


# ---------------------------------------------------------------------------
# Kill-switch: REPO_OPS_NO_LINT_CACHE=1
# ---------------------------------------------------------------------------


def test_no_lint_cache_env_disables_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPO_OPS_NO_LINT_CACHE=1 must bypass read and write."""
    monkeypatch.setenv("REPO_OPS_NO_LINT_CACHE", "1")
    wf = _wf(tmp_path)
    version = "1.0.0"
    # Pre-populate cache with a "pass" entry.
    wf_bytes = wf.read_bytes()
    key = _lint_cache_key(wf_bytes, version)
    lint_cache: dict = {key: {"result": "would be hit", "ts": time.time()}}

    subprocess_called = []

    def fake_run(*_: object, **__: object) -> MagicMock:
        subprocess_called.append(True)
        return _actionlint_pass()

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/actionlint"
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value=version),
    ):
        result = _run_actionlint(wf, tmp_path, lint_cache=lint_cache)

    # Kill-switch means the cache is ignored → subprocess ran.
    assert subprocess_called, "subprocess must run when kill-switch is set"
    # The existing pre-populated cache entry must not have been overwritten
    # (cache write also skipped).
    assert lint_cache[key]["result"] == "would be hit"
    assert result is None


# ---------------------------------------------------------------------------
# lint_level="off" does not consult or write cache
# ---------------------------------------------------------------------------


def test_lint_level_off_no_cache_interaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lint_level='off' must not load or save the cache file."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.write_text(_SIMPLE_WF, encoding="utf-8")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch(
            "repo_ops_upgrade.actions.action_tag_info",
            return_value=None,
        ),
    ):
        scan_actions(tmp_path, apply=False, lint_level="off")

    # No cache file must have been created in "off" mode.
    assert not (tmp_path / "repo-ops" / "actionlint-cache.json").exists()


# ---------------------------------------------------------------------------
# scan_actions integration: cache is persisted after a run
# ---------------------------------------------------------------------------


def test_scan_actions_saves_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scan_actions must write the lint cache file after processing workflows."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("REPO_OPS_NO_LINT_CACHE", raising=False)

    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(_SIMPLE_WF, encoding="utf-8")

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return _actionlint_pass()
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
        patch("repo_ops_upgrade.actions._get_actionlint_version", return_value="1.0"),
    ):
        scan_actions(tmp_path, apply=False, lint_level="block")

    cache_file = tmp_path / "cache" / "repo-ops" / "actionlint-cache.json"
    assert cache_file.exists(), "cache file must be written after scan_actions"
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert len(data) == 1
    entry = next(iter(data.values()))
    assert entry["result"] is None  # lint passed
