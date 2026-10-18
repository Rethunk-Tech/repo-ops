"""Tests for repo_ops_core.filesystem helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ops_core.filesystem import (
    contained_path,
    is_under_root,
    read_json_file,
    read_toml_file,
    should_exclude,
    write_json_atomic,
)


def test_write_json_atomic_happy_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.json"
    payload = {"a": 1, "b": [2, 3]}

    result = write_json_atomic(target, payload)

    assert result == target.resolve()
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    # no leftover temp files in the target directory
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_write_json_atomic_rollback_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "report.json"

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated replace failure")

    monkeypatch.setattr("os.replace", boom)

    with pytest.raises(RuntimeError, match="simulated replace failure"):
        write_json_atomic(target, {"a": 1})

    assert not target.exists()
    # temp file must be cleaned up, not left behind
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []
    assert list(tmp_path.iterdir()) == []


def test_should_exclude_matches_known_fragments() -> None:
    assert should_exclude("/home/me/node_modules/pkg") is True
    assert should_exclude("/usr/local/src/com.github/Rethunk-Tech/repo-ops") is False


def test_read_json_file_happy_and_invalid(tmp_path: Path) -> None:
    good = tmp_path / "ok.json"
    good.write_text('{"x": 1}\n', encoding="utf-8")
    assert read_json_file(good) == {"x": 1}

    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    assert read_json_file(bad) is None

    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2]\n", encoding="utf-8")
    assert read_json_file(arr) is None


def test_read_toml_file_happy_and_invalid(tmp_path: Path) -> None:
    good = tmp_path / "ok.toml"
    good.write_text('name = "demo"\n', encoding="utf-8")
    assert read_toml_file(good) == {"name": "demo"}

    bad = tmp_path / "bad.toml"
    bad.write_text("[[[broken", encoding="utf-8")
    assert read_toml_file(bad) is None


def test_contained_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inside = (root / "lock.json").resolve()
    assert contained_path(root, "lock.json") == inside
    assert contained_path(root, "../outside") is None
    assert contained_path(root, str(tmp_path / "outside")) is None
    assert contained_path(root, "") is None
    outsider = tmp_path / "outside"
    outsider.write_text("x", encoding="utf-8")
    assert not is_under_root(root, outsider)
    assert is_under_root(root, root / "lock.json")
