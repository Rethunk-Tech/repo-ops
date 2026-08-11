"""Tests for Go environment-error classification (go.py classify_go_error).

Each pattern in the catalogue gets one test that verifies:
  - the ``[env] `` prefix is present
  - the remediation hint is present
  - the original stderr is included verbatim

Unclassified errors are tested to pass through unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_go.go import (
    GO_WORK_SYNC_ENV,
    _go_work_dirs,
    _go_work_sync_enabled,
    _run_with_go_work_retry,
    check_go,
    classify_go_error,
    register_go_work_dir,
)
from repo_ops_models import Workspace

# ---------------------------------------------------------------------------
# classify_go_error — pattern matching
# ---------------------------------------------------------------------------


def test_classify_go_work_version_mismatch_listed() -> None:
    """go.work directive older than member — 'listed in go.work file requires go >='."""
    stderr = "go: module example.com/foo listed in go.work file requires go >= 1.25.10"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "go work use" in result
    assert stderr in result


def test_classify_go_work_version_mismatch_reading() -> None:
    """go.work directive older than member — 'reading go.work … requires go'."""
    stderr = "go: reading go.work: requires go >= 1.25.10"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "go work use" in result
    assert stderr in result


def test_classify_go_work_hint_names_directory(tmp_path: Path) -> None:
    """When a go.work dir is registered for a workspace, the hint includes it."""
    ws_path = tmp_path / "module"
    ws_path.mkdir()
    go_work_dir = tmp_path
    register_go_work_dir(ws_path, go_work_dir)

    stderr = "go: module x listed in go.work file requires go >= 1.25.10"
    result = classify_go_error(stderr, ws_path)
    assert str(go_work_dir.resolve()) in result


def test_classify_replace_points_outside() -> None:
    """replace directive points outside the module root."""
    stderr = "go: replace ../bastion-common points outside module root"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "replace" in result
    assert "sibling" in result or "remove" in result
    assert stderr in result


def test_classify_replace_target_no_such_file() -> None:
    """replace target go.mod missing (reading …/go.mod: no such file)."""
    stderr = "go: reading ../bastion-common/go.mod: open ../bastion-common/go.mod: no such file or directory"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "replace" in result.lower() or "missing" in result.lower()
    assert stderr in result


def test_classify_cannot_find_module() -> None:
    """Module not found in registry."""
    stderr = "go: cannot find module providing package example.com/secret"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "GOPROXY" in result or "module" in result.lower()
    assert stderr in result


def test_classify_missing_go_sum_entry() -> None:
    """missing go.sum entry for module providing package."""
    stderr = "verifier: missing go.sum entry for module example.com/foo@v1.2.3"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "go.sum" in result
    assert stderr in result


def test_classify_toolchain_constraint() -> None:
    """Toolchain constraint not met."""
    stderr = "go: toolchain go1.21.0 required; found go1.20.0"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")
    assert "GOTOOLCHAIN" in result or "toolchain" in result.lower()
    assert stderr in result


def test_classify_toolchain_midline() -> None:
    """Toolchain error appearing after a newline."""
    stderr = "some preamble\ngo: toolchain go1.25 required by module"
    result = classify_go_error(stderr)
    assert result.startswith("[env] ")


def test_classify_unrecognized_error_passthrough() -> None:
    """Unrecognised errors are returned unchanged (no [env] prefix)."""
    stderr = "some random go build error that does not match any known pattern"
    result = classify_go_error(stderr)
    assert not result.startswith("[env] ")
    assert result == stderr


def test_classify_empty_stderr_returns_empty() -> None:
    """Empty stderr returns empty string, not '[env] '."""
    assert classify_go_error("") == ""
    assert classify_go_error("   ") == ""


# ---------------------------------------------------------------------------
# go.work detection in discovery (consider_go / _find_go_work)
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    import subprocess as sp

    path.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "init", "-q", str(path)], check=True)


def test_discovery_registers_go_work_dir(tmp_path: Path) -> None:
    """consider_go registers the go.work dir when go.work is above the module."""
    from repo_ops_upgrade.discovery import discover_all

    repo = tmp_path / "myorg" / "myrepo"
    _git_init(repo)
    # go.work at repo root
    (repo / "go.work").write_text("go 1.25\n")
    # module in sub-directory
    module = repo / "services" / "api"
    module.mkdir(parents=True)
    (module / "go.mod").write_text("module example.com/api\n\ngo 1.25\n")

    # Clear registry before test (module-level state from other tests)
    _go_work_dirs.clear()

    discover_all([str(tmp_path)], flat=False)

    assert module.resolve() in _go_work_dirs, (
        f"go.work dir not registered for {module}; registry={dict(_go_work_dirs)}"
    )
    assert _go_work_dirs[module.resolve()] == repo.resolve()


def test_discovery_no_go_work_no_registration(tmp_path: Path) -> None:
    """When there is no go.work anywhere up to the git root, nothing is registered."""
    from repo_ops_upgrade.discovery import discover_all

    repo = tmp_path / "myorg" / "standalone"
    _git_init(repo)
    (repo / "go.mod").write_text("module example.com/standalone\n\ngo 1.22\n")

    _go_work_dirs.clear()
    discover_all([str(tmp_path)], flat=False)

    assert repo.resolve() not in _go_work_dirs


# ---------------------------------------------------------------------------
# REPO_OPS_GO_WORK_SYNC retry logic
# ---------------------------------------------------------------------------


def _make_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="mod",
    )


def test_go_work_sync_uses_repo_config(tmp_path: Path) -> None:
    """A checkout config enables go.work retry behavior without ambient env."""
    ws = _make_ws(tmp_path)

    with (
        patch.dict("os.environ", {}, clear=True),
        patch("repo_ops_go.go.ambient_env", return_value={}),
        patch("repo_ops_go.go.load_repo_config", return_value={"go_work_sync": True}),
        patch("repo_ops_go.go.load_global_config", return_value={}),
    ):
        assert _go_work_sync_enabled(ws) is True


def test_go_work_sync_ambient_env_overrides_repo_config(tmp_path: Path) -> None:
    """Ambient environment config takes precedence over checkout config."""
    ws = _make_ws(tmp_path)

    with (
        patch.dict("os.environ", {}, clear=True),
        patch(
            "repo_ops_go.go.ambient_env",
            return_value={GO_WORK_SYNC_ENV: "0"},
        ),
        patch("repo_ops_go.go.load_repo_config", return_value={"go_work_sync": True}),
        patch("repo_ops_go.go.load_global_config", return_value={}),
    ):
        assert _go_work_sync_enabled(ws) is False


def test_go_work_sync_cli_env_overrides_ambient_and_repo(
    tmp_path: Path,
) -> None:
    """A process-level override takes precedence over ambient and repo config."""
    ws = _make_ws(tmp_path)

    with (
        patch.dict("os.environ", {GO_WORK_SYNC_ENV: "1"}, clear=True),
        patch(
            "repo_ops_go.go.ambient_env",
            return_value={GO_WORK_SYNC_ENV: "0"},
        ),
        patch("repo_ops_go.go.load_repo_config", return_value={"go_work_sync": False}),
        patch("repo_ops_go.go.load_global_config", return_value={}),
    ):
        assert _go_work_sync_enabled(ws) is True


def test_go_work_sync_off_no_retry(tmp_path: Path) -> None:
    """Without REPO_OPS_GO_WORK_SYNC=1, a go.work mismatch error is NOT retried."""
    ws = _make_ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    register_go_work_dir(tmp_path, go_work_dir)

    call_count = 0

    def fake_run(cmd, cwd, timeout=0):
        nonlocal call_count
        call_count += 1
        return type(
            "Proc",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "go: module x listed in go.work file requires go >= 1.25.10",
            },
        )()

    with (
        patch.dict("os.environ", {GO_WORK_SYNC_ENV: "0"}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        result = _run_with_go_work_retry(
            ["go", "list", "-m", "-u", "-json", "all"], ws, timeout=60
        )

    # No retry: run() called exactly once
    assert call_count == 1
    assert result.returncode != 0


def test_go_work_sync_on_retries_once_on_mismatch(tmp_path: Path) -> None:
    """With REPO_OPS_GO_WORK_SYNC=1, mismatch triggers go work use + one retry."""
    ws = _make_ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    register_go_work_dir(tmp_path, go_work_dir)

    run_calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        run_calls.append(list(cmd))
        if cmd == ["go", "work", "use"]:
            # go work use succeeds
            return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if len([c for c in run_calls if c != ["go", "work", "use"]]) == 1:
            # First real command fails with mismatch
            return type(
                "Proc",
                (),
                {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "go: module x listed in go.work file requires go >= 1.25.10",
                },
            )()
        # Retry succeeds
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with (
        patch.dict("os.environ", {GO_WORK_SYNC_ENV: "1"}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        result = _run_with_go_work_retry(
            ["go", "list", "-m", "-u", "-json", "all"], ws, timeout=60
        )

    assert ["go", "work", "use"] in run_calls, "go work use was not called"
    # run_calls includes: original cmd, go work use, retry cmd
    real_calls = [c for c in run_calls if c != ["go", "work", "use"]]
    assert len(real_calls) == 2, f"expected 2 real cmd calls; got {real_calls}"
    assert result.returncode == 0


def test_go_work_sync_on_no_retry_for_unrelated_error(tmp_path: Path) -> None:
    """REPO_OPS_GO_WORK_SYNC=1 does NOT retry for non-go.work errors."""
    ws = _make_ws(tmp_path)
    call_count = 0

    def fake_run(cmd, cwd, timeout=0):
        nonlocal call_count
        call_count += 1
        return type(
            "Proc",
            (),
            {"returncode": 1, "stdout": "", "stderr": "go: cannot find module foo"},
        )()

    with (
        patch.dict("os.environ", {GO_WORK_SYNC_ENV: "1"}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        _run_with_go_work_retry(
            ["go", "list", "-m", "-u", "-json", "all"], ws, timeout=60
        )

    assert call_count == 1


def test_go_work_sync_on_no_retry_when_go_work_use_fails(tmp_path: Path) -> None:
    """When go work use itself fails, no retry of the original command."""
    ws = _make_ws(tmp_path)
    go_work_dir = tmp_path / "workspace"
    go_work_dir.mkdir()
    register_go_work_dir(tmp_path, go_work_dir)

    run_calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=0):
        run_calls.append(list(cmd))
        # Everything fails
        return type(
            "Proc",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "go: module x listed in go.work file requires go >= 1.25.10",
            },
        )()

    with (
        patch.dict("os.environ", {GO_WORK_SYNC_ENV: "1"}),
        patch("repo_ops_go.go.run", side_effect=fake_run),
    ):
        result = _run_with_go_work_retry(
            ["go", "list", "-m", "-u", "-json", "all"], ws, timeout=60
        )

    # go work use was attempted, but retry should not have been issued
    # (only 2 calls: original + go work use)
    assert ["go", "work", "use"] in run_calls
    real_calls = [c for c in run_calls if c != ["go", "work", "use"]]
    assert len(real_calls) == 1, f"expected 1 real cmd call; got {real_calls}"
    assert result.returncode != 0


def test_classify_errors_propagated_via_check_go(tmp_path: Path) -> None:
    """check_go raises RuntimeError with [env] prefix for env-class errors."""
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=0):
        return type(
            "Proc",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "go: module x listed in go.work file requires go >= 1.25.10",
            },
        )()

    with (
        patch.dict(
            "os.environ", {"REPO_OPS_GO_UPGRADE_MODE": "patch", GO_WORK_SYNC_ENV: "0"}
        ),
        patch("repo_ops_go.go.run", side_effect=fake_run),
        pytest.raises(RuntimeError, match=r"\[env\]"),
    ):
        check_go(ws)
