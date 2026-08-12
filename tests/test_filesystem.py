"""Tests for repo_ops_core.filesystem helpers."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
from repo_ops_core import filesystem as filesystem_mod
from repo_ops_core.filesystem import (
    load_toml_parser,
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


def test_read_toml_file_skips_when_parser_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("jobs = 3\n", encoding="utf-8")
    monkeypatch.setattr(filesystem_mod, "_tomllib", None)

    assert read_toml_file(path) is None


def test_tomli_import_error_marks_parser_unavailable() -> None:
    original_import = builtins.__import__

    def broken_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[object, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "tomllib":
            raise ModuleNotFoundError("tomllib unavailable")
        if name == "tomli":
            raise ImportError("tomli is broken")
        return original_import(name, globals, locals, fromlist, level)

    assert load_toml_parser(importer=broken_import) is None
