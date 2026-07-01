"""Tests for repo_ops_core.filesystem.write_json_atomic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from repo_ops_core.filesystem import write_json_atomic


def test_write_json_atomic_happy_path(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.json"
    payload = {"a": 1, "b": [2, 3]}

    result = write_json_atomic(target, payload)

    assert result == target.resolve()
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    # no leftover temp files in the target directory
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_write_json_atomic_rollback_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
