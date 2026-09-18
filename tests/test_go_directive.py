"""Tests for go.py directive management.

Covers:
  - go.work directive sync: plan (_plan_go_work_directive_changes) and apply
    (_apply_go_work_directive) paths via mocked subprocess / filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_go.go import (
    _apply_go_work_directive,
    _parse_go_directive,
    _plan_go_work_directive_changes,
    register_go_work_dir,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gomod_json(go_ver: str = "1.22", toolchain: str | None = None) -> str:
    doc: dict = {
        "Module": {"Path": "example.com/main"},
        "Go": go_ver,
        "Require": [],
    }
    if toolchain is not None:
        doc["Toolchain"] = f"go{toolchain}"
    return json.dumps(doc)


# ---------------------------------------------------------------------------
# _parse_go_directive
# ---------------------------------------------------------------------------


def test_parse_go_directive_two_part() -> None:
    assert _parse_go_directive("module foo\n\ngo 1.22\n") == "1.22"


def test_parse_go_directive_three_part() -> None:
    assert _parse_go_directive("go 1.25.10\n") == "1.25.10"


def test_parse_go_directive_no_match() -> None:
    assert _parse_go_directive("toolchain go1.22.0\n") is None


def test_parse_go_directive_inline_not_matched() -> None:
    # Must be at start of line; "// go 1.22" should not match.
    assert _parse_go_directive("// go 1.22\n") is None


# ---------------------------------------------------------------------------
# _plan_go_work_directive_changes — textual parse, no subprocess
# ---------------------------------------------------------------------------


def _write_go_work(go_work_dir: Path, go_ver: str, members: list[str]) -> None:
    lines = [f"go {go_ver}\n"]
    if members:
        lines.append("use (\n")
        for m in members:
            lines.append(f"\t{m}\n")
        lines.append(")\n")
    (go_work_dir / "go.work").write_text("".join(lines))


def test_plan_go_work_no_change_needed(tmp_path: Path) -> None:
    """go.work is already at or above all member requirements — no change."""
    mod = tmp_path / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.22\n")
    _write_go_work(tmp_path, "1.22", ["./api"])
    changes = _plan_go_work_directive_changes(tmp_path)
    assert changes == []


def test_plan_go_work_change_needed(tmp_path: Path) -> None:
    """Member requires 1.25 but go.work declares 1.22 — emit one Change."""
    mod = tmp_path / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(tmp_path, "1.22", ["./api"])
    changes = _plan_go_work_directive_changes(tmp_path)
    assert len(changes) == 1
    assert changes[0].name == "go.work go directive"
    assert changes[0].from_ver == "1.22"
    assert changes[0].to == "1.25"


def test_plan_go_work_picks_highest_member(tmp_path: Path) -> None:
    """When multiple members have different requirements, pick the highest."""
    for name, ver in [("svc_a", "1.23"), ("svc_b", "1.25"), ("svc_c", "1.24")]:
        d = tmp_path / name
        d.mkdir()
        (d / "go.mod").write_text(f"module example.com/{name}\n\ngo {ver}\n")
    _write_go_work(tmp_path, "1.22", [f"./{n}" for n in ("svc_a", "svc_b", "svc_c")])
    changes = _plan_go_work_directive_changes(tmp_path)
    assert len(changes) == 1
    assert changes[0].to == "1.25"


def test_plan_go_work_patch_version(tmp_path: Path) -> None:
    """Three-component patch versions are compared correctly."""
    mod = tmp_path / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25.10\n")
    _write_go_work(tmp_path, "1.25.3", ["./api"])
    changes = _plan_go_work_directive_changes(tmp_path)
    assert len(changes) == 1
    assert changes[0].to == "1.25.10"


def test_plan_go_work_missing_go_work_file(tmp_path: Path) -> None:
    """Missing go.work file → no changes, no exception."""
    changes = _plan_go_work_directive_changes(tmp_path)
    assert changes == []


def test_plan_go_work_no_go_directive_in_work(tmp_path: Path) -> None:
    """go.work without a go directive line → no changes."""
    (tmp_path / "go.work").write_text("use ./api\n")
    mod = tmp_path / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    changes = _plan_go_work_directive_changes(tmp_path)
    assert changes == []


# ---------------------------------------------------------------------------
# _apply_go_work_directive — runs go work use when needed
# ---------------------------------------------------------------------------


def test_apply_go_work_directive_runs_when_stale(
    tmp_path: Path, make_workspace, make_proc, fake_run: Any
) -> None:
    """_apply_go_work_directive calls go work use when go.work is behind a member."""
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="mod")
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_go_work_directive(ws)

    assert ["go", "work", "use"] in fake_run.calls, (
        f"go work use not called; calls={fake_run.calls}"
    )


def test_apply_go_work_directive_skips_when_current(
    tmp_path: Path, make_workspace, make_proc, fake_run: Any
) -> None:
    """_apply_go_work_directive does NOT call go work use when directive is current."""
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="mod")
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.22\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_go_work_directive(ws)

    assert ["go", "work", "use"] not in fake_run.calls


def test_apply_go_work_directive_no_go_work_dir_registered(
    tmp_path: Path, make_workspace, make_proc, fake_run: Any
) -> None:
    """_apply_go_work_directive is a no-op when no go.work dir is registered."""
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="mod")

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_go_work_directive(ws)

    assert fake_run.calls == []


def test_apply_go_work_directive_raises_on_failure(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    """When go work use fails, RuntimeError is raised."""
    ws = make_workspace(tmp_path, ecosystem="go", manager="go", label="mod")
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    with (
        patch(
            "repo_ops_go.go.run",
            return_value=make_proc(1, stderr="go work use exploded"),
        ),
        pytest.raises(RuntimeError),
    ):
        _apply_go_work_directive(ws)
