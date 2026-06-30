"""Tests for REPO_OPS_ACTIONS_LINT_LEVEL / lint_level parameter (issue #117).

Regression: SC2034 in an unrelated ``run:`` block used to silently block a
softprops/action-gh-release bump because actionlint all-or-nothing ``continue``
skipped ALL pin bumps for that workflow.

The ``warn`` level records the fail row + prints WARN but proceeds with bumps.
The ``off`` level skips actionlint entirely.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_upgrade.actions import (
    _classify_actionlint_severity,
    _read_lint_level_env,
    scan_actions,
)

_CHECKOUT_WF = """\
name: CI
on: push
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2.0.0
      - run: unused_var=1
"""


def _wf_dir(tmp_path: Path, content: str = _CHECKOUT_WF) -> tuple[Path, Path]:
    """Write a workflow file and return (repo_root, workflow_path)."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return tmp_path, wf


def _tag_info_checkout(action: str, **_: object) -> dict | None:
    if action == "actions/checkout":
        # v2 bare tag exists → normalize v2.0.0 → v2 is actionable.
        return {
            "bare_majors": {2},
            "max_major": 2,
            "all_tags": {"v2", "v2.0.0"},
            "sha_to_tags": {},
        }
    return None


def _actionlint_fail(*_args: object, **_kwargs: object) -> MagicMock:
    """Simulate actionlint returning a non-zero exit code (e.g. SC2034 hit)."""
    return MagicMock(returncode=1, stdout="", stderr="SC2034: var unused")


def _actionlint_pass(*_args: object, **_kwargs: object) -> MagicMock:
    return MagicMock(returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# lint_level="block" (default) — existing behaviour: bumps skipped on failure
# ---------------------------------------------------------------------------


def test_lint_level_block_skips_pin_bumps(tmp_path: Path) -> None:
    """block (default): actionlint failure records fail row and skips pin bumps."""
    repo, wf = _wf_dir(tmp_path)
    original = wf.read_text(encoding="utf-8")

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return _actionlint_fail()
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        bumps, scanned = scan_actions(repo, apply=True, lint_level="block")

    assert scanned == 1
    # Must have an actionlint fail row.
    lint_rows = [b for b in bumps if b.action == "(actionlint)"]
    assert lint_rows, "expected actionlint fail row"
    assert lint_rows[0].status == "fail"
    # No checkout bump emitted — skipped by block.
    checkout_rows = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout_rows == [], "expected no pin bump in block mode"
    # File unchanged — block prevented any rewrite.
    assert wf.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# lint_level="warn" — records fail row + WARN but proceeds with pin bumps
# ---------------------------------------------------------------------------


def test_lint_level_warn_proceeds_with_bumps(tmp_path: Path) -> None:
    """warn: actionlint failure records fail row but pin bumps still run."""
    repo, _wf = _wf_dir(tmp_path)

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return _actionlint_fail()
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        bumps, scanned = scan_actions(repo, apply=True, lint_level="warn")

    assert scanned == 1
    # Must still have the actionlint warn row (non-fatal at lint_level=warn).
    lint_rows = [b for b in bumps if b.action == "(actionlint)"]
    assert lint_rows, "expected actionlint warn row"
    assert lint_rows[0].status == "warn"
    # Checkout bump MUST be present (proceeded past lint finding).
    checkout_rows = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout_rows, "expected pin bump row in warn mode"


def test_lint_level_warn_applies_rewrite(tmp_path: Path) -> None:
    """warn: actionlint failure + apply=True still rewrites the workflow."""
    repo, wf = _wf_dir(tmp_path)

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return _actionlint_fail()
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        scan_actions(repo, apply=True, lint_level="warn")

    text = wf.read_text(encoding="utf-8")
    # v2.0.0 must have been normalized to bare v2.
    assert "actions/checkout@v2" in text, text
    assert "actions/checkout@v2.0.0" not in text, text


def test_lint_level_warn_prints_warn_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """warn: a WARN message is printed to stderr when lint fails but bumps proceed."""
    repo, _ = _wf_dir(tmp_path)

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return _actionlint_fail()
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        scan_actions(repo, apply=False, lint_level="warn")

    err = capsys.readouterr().err
    assert "WARN" in err
    assert "lint_level=warn" in err


# ---------------------------------------------------------------------------
# lint_level="off" — skips actionlint entirely
# ---------------------------------------------------------------------------


def test_lint_level_off_skips_actionlint(tmp_path: Path) -> None:
    """off: actionlint is never called; pin bumps proceed unconditionally."""
    repo, _wf = _wf_dir(tmp_path)
    actionlint_called = []

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            actionlint_called.append(True)
            return _actionlint_fail()
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        bumps, scanned = scan_actions(repo, apply=True, lint_level="off")

    assert scanned == 1
    assert not actionlint_called, "actionlint must not be called in off mode"
    # No actionlint fail row.
    lint_rows = [b for b in bumps if b.action == "(actionlint)"]
    assert lint_rows == []
    # Checkout bump present.
    checkout_rows = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout_rows, "expected pin bump row in off mode"


def test_lint_level_off_applies_rewrite(tmp_path: Path) -> None:
    """off: actionlint skipped + apply=True still rewrites the workflow."""
    repo, wf = _wf_dir(tmp_path)

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        scan_actions(repo, apply=True, lint_level="off")

    text = wf.read_text(encoding="utf-8")
    # v2.0.0 must have been normalized to bare v2.
    assert "actions/checkout@v2" in text, text
    assert "actions/checkout@v2.0.0" not in text, text


# ---------------------------------------------------------------------------
# Regression: shellcheck :warning: under lint_level=block must be non-fatal
# ---------------------------------------------------------------------------

# A real actionlint finding line for a shellcheck warning (has a proper header).
_SC_WARNING_STDERR = (
    ".github/workflows/ci.yml:26:7: shellcheck reported issue in this script: "
    "SC2034:warning:7:1: i appears unused. Verify use (or export if used externally) [shellcheck]"
)

# A native actionlint finding (no "shellcheck reported issue").
_NATIVE_ERROR_STDERR = (
    ".github/workflows/ci.yml:10:9: "
    'property "foo" is not defined in object type {} [expression]'
)


def test_lint_level_block_shellcheck_warning_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """block: a shellcheck :warning: finding must NOT be fatal; pin bumps proceed."""
    monkeypatch.setenv("REPO_OPS_NO_LINT_CACHE", "1")
    repo, wf = _wf_dir(tmp_path)

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return MagicMock(returncode=1, stdout="", stderr=_SC_WARNING_STDERR)
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        bumps, scanned = scan_actions(repo, apply=True, lint_level="block")

    assert scanned == 1
    # (actionlint) row present but status is warn, NOT fail.
    lint_rows = [b for b in bumps if b.action == "(actionlint)"]
    assert lint_rows, "expected actionlint warn row"
    assert lint_rows[0].status == "warn", (
        f"shellcheck warning must be non-fatal; got status={lint_rows[0].status!r}"
    )
    # Checkout bump present — bumps proceeded.
    checkout_rows = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout_rows, "expected checkout pin bump row (bumps must proceed)"
    # File was rewritten.
    text = wf.read_text(encoding="utf-8")
    assert "actions/checkout@v2" in text, text
    assert "actions/checkout@v2.0.0" not in text, text


def test_lint_level_block_native_actionlint_error_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """block: a native actionlint finding (non-shellcheck) remains fatal."""
    monkeypatch.setenv("REPO_OPS_NO_LINT_CACHE", "1")
    repo, wf = _wf_dir(tmp_path)
    original = wf.read_text(encoding="utf-8")

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            return MagicMock(returncode=1, stdout="", stderr=_NATIVE_ERROR_STDERR)
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        bumps, scanned = scan_actions(repo, apply=True, lint_level="block")

    assert scanned == 1
    # (actionlint) row is fatal.
    lint_rows = [b for b in bumps if b.action == "(actionlint)"]
    assert lint_rows, "expected actionlint fail row"
    assert lint_rows[0].status == "fail", (
        f"native actionlint error must be fatal; got status={lint_rows[0].status!r}"
    )
    # No checkout bump — block skipped bumps.
    checkout_rows = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout_rows == [], "expected no pin bump when native error is fatal"
    # File unchanged.
    assert wf.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Unit tests for _classify_actionlint_severity
# ---------------------------------------------------------------------------


def test_classify_empty_is_error() -> None:
    assert _classify_actionlint_severity("") == "error"
    assert _classify_actionlint_severity("   ") == "error"


def test_classify_sc_warning_is_warning() -> None:
    assert _classify_actionlint_severity(_SC_WARNING_STDERR) == "warning"


def test_classify_native_actionlint_is_error() -> None:
    assert _classify_actionlint_severity(_NATIVE_ERROR_STDERR) == "error"


def test_classify_shellcheck_error_severity_is_error() -> None:
    line = (
        "f.yml:1:1: shellcheck reported issue in this script: "
        "SC1009:error:1:1: parse error [shellcheck]"
    )
    assert _classify_actionlint_severity(line) == "error"


def test_classify_timeout_message_is_error() -> None:
    assert _classify_actionlint_severity("actionlint timed out") == "error"


# ---------------------------------------------------------------------------
# Env-var read: REPO_OPS_ACTIONS_LINT_LEVEL
# ---------------------------------------------------------------------------


def test_read_lint_level_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent env var → 'block'."""
    monkeypatch.delenv("REPO_OPS_ACTIONS_LINT_LEVEL", raising=False)
    assert _read_lint_level_env() == "block"


def test_read_lint_level_env_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_OPS_ACTIONS_LINT_LEVEL", "warn")
    assert _read_lint_level_env() == "warn"


def test_read_lint_level_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_OPS_ACTIONS_LINT_LEVEL", "off")
    assert _read_lint_level_env() == "off"


def test_read_lint_level_env_unknown_defaults_to_block(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown value falls back to 'block' with a WARN message."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_LINT_LEVEL", "verbose")
    result = _read_lint_level_env()
    assert result == "block"
    err = capsys.readouterr().err
    assert "WARN" in err
    assert "verbose" in err


def test_lint_level_env_used_when_param_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When lint_level param is None, env var drives behaviour."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_LINT_LEVEL", "off")
    repo, _wf = _wf_dir(tmp_path)
    actionlint_called = []

    def fake_run(cmd: list[str], *a: object, **kw: object) -> MagicMock:
        if cmd and cmd[0] == "actionlint":
            actionlint_called.append(True)
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}",
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info", side_effect=_tag_info_checkout
        ),
    ):
        scan_actions(repo, apply=False)  # no lint_level arg → reads env

    assert not actionlint_called, "actionlint must not be called when env=off"
