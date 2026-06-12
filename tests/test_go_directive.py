"""Tests for go.py directive management (issue #120).

Covers:
  - go.work directive sync: plan (_plan_go_work_directive_changes) and apply
    (_apply_go_work_directive) paths via mocked subprocess / filesystem.
  - Fleet directive bumps: plan (_plan_fleet_directive_changes) and apply
    (_apply_fleet_directive) paths via REPO_OPS_GO_DIRECTIVE /
    REPO_OPS_GO_TOOLCHAIN env vars.
  - Issue #118: timeout calls use resolve_timeout_check / resolve_timeout_apply.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_go.go import (
    GO_DIRECTIVE_ENV,
    GO_TOOLCHAIN_ENV,
    _apply_fleet_directive,
    _apply_go_work_directive,
    _parse_go_directive,
    _plan_fleet_directive_changes,
    _plan_go_work_directive_changes,
    apply_go,
    check_go,
    register_go_work_dir,
)
from repo_ops_models import Workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )


def _ok_proc(stdout: str = "", stderr: str = ""):
    return type("Proc", (), {"returncode": 0, "stdout": stdout, "stderr": stderr})()


def _fail_proc(stderr: str = "boom"):
    return type("Proc", (), {"returncode": 1, "stdout": "", "stderr": stderr})()


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
# check_go — go.work directive changes are prepended
# ---------------------------------------------------------------------------


def test_check_go_includes_work_directive_change(tmp_path: Path) -> None:
    """check_go prepends go.work directive change when go.work is stale."""
    ws = _ws(tmp_path)
    # Register a go.work dir and write fixture files.
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    # go list returns no dep updates; we only care about the directive change.
    def fake_run(cmd, cwd, timeout=0):
        return _ok_proc(stdout="")

    with (
        patch.dict("os.environ", {"REPO_OPS_GO_UPGRADE_MODE": "patch"}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = check_go(ws)

    assert any(c.name == "go.work go directive" for c in changes), (
        f"expected go.work directive change; got {[c.name for c in changes]}"
    )


# ---------------------------------------------------------------------------
# _apply_go_work_directive — runs go work use when needed
# ---------------------------------------------------------------------------


def test_apply_go_work_directive_runs_when_stale(tmp_path: Path) -> None:
    """_apply_go_work_directive calls go work use when go.work is behind a member."""
    ws = _ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc()

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_go_work_directive(ws)

    assert ["go", "work", "use"] in calls_recorded, (
        f"go work use not called; calls={calls_recorded}"
    )


def test_apply_go_work_directive_skips_when_current(tmp_path: Path) -> None:
    """_apply_go_work_directive does NOT call go work use when directive is current."""
    ws = _ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.22\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc()

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_go_work_directive(ws)

    assert ["go", "work", "use"] not in calls_recorded


def test_apply_go_work_directive_no_go_work_dir_registered(tmp_path: Path) -> None:
    """_apply_go_work_directive is a no-op when no go.work dir is registered."""
    ws = _ws(tmp_path)
    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc()

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_go_work_directive(ws)

    assert calls_recorded == []


def test_apply_go_work_directive_raises_on_failure(tmp_path: Path) -> None:
    """When go work use fails, RuntimeError is raised."""
    ws = _ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    with (
        patch("repo_ops_go.go.run", return_value=_fail_proc("go work use exploded")),
        pytest.raises(RuntimeError),
    ):
        _apply_go_work_directive(ws)


# ---------------------------------------------------------------------------
# _plan_fleet_directive_changes — REPO_OPS_GO_DIRECTIVE / REPO_OPS_GO_TOOLCHAIN
# ---------------------------------------------------------------------------


def test_plan_fleet_directive_no_env_vars(tmp_path: Path) -> None:
    """No env vars set → empty list (zero behaviour change)."""
    ws = _ws(tmp_path)
    with patch.dict("os.environ", {}, clear=False):
        # Ensure both vars are absent.
        for k in (GO_DIRECTIVE_ENV, GO_TOOLCHAIN_ENV):
            import os

            os.environ.pop(k, None)
        changes = _plan_fleet_directive_changes(ws)
    assert changes == []


def test_plan_fleet_directive_go_upgrade_needed(tmp_path: Path) -> None:
    """REPO_OPS_GO_DIRECTIVE=1.25 with module at 1.22 → one Change."""
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        return _ok_proc(stdout=_gomod_json("1.22"))

    with (
        patch.dict("os.environ", {GO_DIRECTIVE_ENV: "1.25", GO_TOOLCHAIN_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _plan_fleet_directive_changes(ws)

    assert len(changes) == 1
    assert changes[0].name == "go directive"
    assert changes[0].from_ver == "1.22"
    assert changes[0].to == "1.25"


def test_plan_fleet_directive_already_current(tmp_path: Path) -> None:
    """Module already at target → no change emitted."""
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        return _ok_proc(stdout=_gomod_json("1.25"))

    with (
        patch.dict("os.environ", {GO_DIRECTIVE_ENV: "1.25", GO_TOOLCHAIN_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _plan_fleet_directive_changes(ws)

    assert changes == []


def test_plan_fleet_directive_toolchain_upgrade_needed(tmp_path: Path) -> None:
    """REPO_OPS_GO_TOOLCHAIN=1.25.10 with module at 1.25.3 → one Change."""
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        return _ok_proc(stdout=_gomod_json("1.25", toolchain="1.25.3"))

    with (
        patch.dict("os.environ", {GO_TOOLCHAIN_ENV: "1.25.10", GO_DIRECTIVE_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _plan_fleet_directive_changes(ws)

    assert len(changes) == 1
    assert changes[0].name == "go toolchain"
    assert changes[0].from_ver == "1.25.3"
    assert changes[0].to == "1.25.10"


def test_plan_fleet_toolchain_normalises_go_prefix(tmp_path: Path) -> None:
    """REPO_OPS_GO_TOOLCHAIN=go1.25.10 (with go prefix) is treated identically."""
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        return _ok_proc(stdout=_gomod_json("1.25", toolchain="1.25.3"))

    with (
        patch.dict("os.environ", {GO_TOOLCHAIN_ENV: "go1.25.10", GO_DIRECTIVE_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _plan_fleet_directive_changes(ws)

    assert len(changes) == 1
    assert changes[0].to == "1.25.10"


def test_plan_fleet_toolchain_no_existing_directive(tmp_path: Path) -> None:
    """No current toolchain directive in go.mod → treated as needing the bump."""
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        # Toolchain key absent from JSON.
        return _ok_proc(stdout=_gomod_json("1.25"))

    with (
        patch.dict("os.environ", {GO_TOOLCHAIN_ENV: "1.25.10", GO_DIRECTIVE_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _plan_fleet_directive_changes(ws)

    assert len(changes) == 1
    assert changes[0].name == "go toolchain"
    assert changes[0].from_ver == "(none)"


def test_plan_fleet_directive_both_vars(tmp_path: Path) -> None:
    """Both REPO_OPS_GO_DIRECTIVE and REPO_OPS_GO_TOOLCHAIN set → two changes."""
    ws = _ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        return _ok_proc(stdout=_gomod_json("1.22", toolchain="1.22.0"))

    with (
        patch.dict(
            "os.environ", {GO_DIRECTIVE_ENV: "1.25", GO_TOOLCHAIN_ENV: "1.25.10"}
        ),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        changes = _plan_fleet_directive_changes(ws)

    names = {c.name for c in changes}
    assert "go directive" in names
    assert "go toolchain" in names


# ---------------------------------------------------------------------------
# _apply_fleet_directive — apply path via mocked subprocess
# ---------------------------------------------------------------------------


def test_apply_fleet_directive_runs_mod_edit_go(tmp_path: Path) -> None:
    """REPO_OPS_GO_DIRECTIVE triggers go mod edit -go=<ver> + go mod tidy."""
    ws = _ws(tmp_path)
    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc(stdout=_gomod_json("1.22"))

    with (
        patch.dict("os.environ", {GO_DIRECTIVE_ENV: "1.25", GO_TOOLCHAIN_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        _apply_fleet_directive(ws)

    assert ["go", "mod", "edit", "-go=1.25"] in calls_recorded
    assert ["go", "mod", "tidy"] in calls_recorded


def test_apply_fleet_directive_runs_mod_edit_toolchain(tmp_path: Path) -> None:
    """REPO_OPS_GO_TOOLCHAIN triggers go mod edit -toolchain=go<ver> + go mod tidy."""
    ws = _ws(tmp_path)
    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc(stdout=_gomod_json("1.25"))

    with (
        patch.dict("os.environ", {GO_TOOLCHAIN_ENV: "1.25.10", GO_DIRECTIVE_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        _apply_fleet_directive(ws)

    assert ["go", "mod", "edit", "-toolchain=go1.25.10"] in calls_recorded
    assert ["go", "mod", "tidy"] in calls_recorded


def test_apply_fleet_directive_no_op_when_env_unset(tmp_path: Path) -> None:
    """No env vars set → no subprocess calls at all."""
    ws = _ws(tmp_path)
    import os

    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc()

    os.environ.pop(GO_DIRECTIVE_ENV, None)
    os.environ.pop(GO_TOOLCHAIN_ENV, None)

    with patch("repo_ops_go.go.run", side_effect=fake_run):
        _apply_fleet_directive(ws)

    assert calls_recorded == []


def test_apply_fleet_directive_no_op_when_already_current(tmp_path: Path) -> None:
    """Target matches current directive → no edit/tidy calls."""
    ws = _ws(tmp_path)
    calls_recorded: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        calls_recorded.append(list(cmd))
        return _ok_proc(stdout=_gomod_json("1.25"))

    with (
        patch.dict("os.environ", {GO_DIRECTIVE_ENV: "1.25", GO_TOOLCHAIN_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        _apply_fleet_directive(ws)

    assert ["go", "mod", "edit", "-go=1.25"] not in calls_recorded


def test_apply_fleet_directive_raises_on_mod_edit_failure(tmp_path: Path) -> None:
    """go mod edit failure raises RuntimeError."""
    ws = _ws(tmp_path)
    call_n = [0]

    def fake_run(cmd, cwd, timeout=0):
        call_n[0] += 1
        if cmd[:3] == ["go", "mod", "edit"] and call_n[0] > 1:
            # First call is go mod edit -json (probe); second is -go= (edit).
            return _fail_proc("go mod edit failed")
        return _ok_proc(stdout=_gomod_json("1.22"))

    with (
        patch.dict("os.environ", {GO_DIRECTIVE_ENV: "1.25", GO_TOOLCHAIN_ENV: ""}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
        pytest.raises(RuntimeError),
    ):
        _apply_fleet_directive(ws)


# ---------------------------------------------------------------------------
# apply_go integration — directive phases run before dep upgrade
# ---------------------------------------------------------------------------


def test_apply_go_calls_work_directive_before_dep_upgrade(tmp_path: Path) -> None:
    """apply_go: _apply_go_work_directive is called before go get."""
    ws = _ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    mod = go_work_dir / "api"
    mod.mkdir()
    (mod / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")
    _write_go_work(go_work_dir, "1.22", ["./api"])
    register_go_work_dir(tmp_path, go_work_dir)

    call_order: list[str] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["go", "work", "use"]:
            call_order.append("go_work_use")
        elif cmd[:2] == ["go", "get"]:
            call_order.append("go_get")
        return _ok_proc()

    with (
        patch.dict(
            "os.environ",
            {
                "REPO_OPS_GO_UPGRADE_MODE": "patch",
                GO_DIRECTIVE_ENV: "",
                GO_TOOLCHAIN_ENV: "",
            },
        ),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        apply_go(ws)

    assert "go_work_use" in call_order
    assert call_order.index("go_work_use") < call_order.index("go_get")


def test_apply_go_fleet_directive_runs_before_dep_upgrade(tmp_path: Path) -> None:
    """apply_go: fleet directive mod edit runs before go get."""
    ws = _ws(tmp_path)
    call_order: list[str] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd[:3] == ["go", "mod", "edit"] and "-go=" in " ".join(cmd):
            call_order.append("mod_edit_go")
        elif cmd[:2] == ["go", "get"]:
            call_order.append("go_get")
        # Return valid JSON for go mod edit -json probes.
        return _ok_proc(stdout=_gomod_json("1.22"))

    with (
        patch.dict(
            "os.environ",
            {
                "REPO_OPS_GO_UPGRADE_MODE": "patch",
                GO_DIRECTIVE_ENV: "1.25",
                GO_TOOLCHAIN_ENV: "",
            },
        ),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        apply_go(ws)

    assert "mod_edit_go" in call_order
    assert call_order.index("mod_edit_go") < call_order.index("go_get")


# ---------------------------------------------------------------------------
# Issue #118 — resolve_timeout_check/apply used (not bare constants)
# ---------------------------------------------------------------------------


def test_issue_118_timeout_resolvers_used_in_gomod_require_paths(
    tmp_path: Path,
) -> None:
    """_gomod_require_paths uses resolve_timeout_check(), honoring env override."""
    from repo_ops_go.go import _gomod_require_paths
    from repo_ops_upgrade.constants import TIMEOUT_CHECK

    ws = _ws(tmp_path)
    captured: list[int] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd[:3] == ["go", "mod", "edit"]:
            captured.append(timeout)
        return _ok_proc(stdout=json.dumps({"Module": {"Path": "x"}, "Require": []}))

    custom = TIMEOUT_CHECK + 100

    with (
        patch.dict("os.environ", {"REPO_OPS_TIMEOUT_CHECK": str(custom)}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        _gomod_require_paths(ws)

    assert captured, "run() not called"
    assert captured[0] == custom, f"expected {custom}, got {captured[0]}"


def test_issue_118_timeout_resolvers_used_in_apply_go_tidy(tmp_path: Path) -> None:
    """apply_go uses resolve_timeout_apply() for go mod tidy, honoring env override."""
    from repo_ops_upgrade.constants import TIMEOUT_APPLY

    ws = _ws(tmp_path)
    tidy_timeouts: list[int] = []

    def fake_run(cmd, cwd, timeout=0):
        if cmd == ["go", "mod", "tidy"]:
            tidy_timeouts.append(timeout)
        return _ok_proc()

    custom = TIMEOUT_APPLY + 200

    with (
        patch.dict(
            "os.environ",
            {
                "REPO_OPS_GO_UPGRADE_MODE": "patch",
                "REPO_OPS_TIMEOUT_APPLY": str(custom),
                GO_DIRECTIVE_ENV: "",
                GO_TOOLCHAIN_ENV: "",
            },
        ),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        apply_go(ws)

    assert tidy_timeouts, "go mod tidy not called"
    assert all(t == custom for t in tidy_timeouts), (
        f"expected all tidy timeouts={custom}, got {tidy_timeouts}"
    )
