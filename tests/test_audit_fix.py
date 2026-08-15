"""Tests for audit --fix: RemediationItem → apply adaptor.

All subprocess/apply calls are mocked; no real network or file mutations.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_audit.audit import (
    ApplyOutcome,
    RemediationItem,
    _eco_to_manager,
    _try_register_manager,
    apply_remediations,
    build_audit_parser,
    main,
)

# ---------------------------------------------------------------------------
# Adaptor helpers
# ---------------------------------------------------------------------------


def test_eco_to_manager_mapping() -> None:
    """Each audit ecosystem maps to the expected upgrade-registry manager name."""
    assert _eco_to_manager("py-uv") == "uv"
    assert _eco_to_manager("rust") == "cargo"
    assert _eco_to_manager("go") == "go"
    assert _eco_to_manager("node") == "npm"  # default


def test_eco_to_manager_passthrough_unknown() -> None:
    """Unknown ecosystem passes through unchanged."""
    assert _eco_to_manager("unknown-eco") == "unknown-eco"


# ---------------------------------------------------------------------------
# _try_register_manager
# ---------------------------------------------------------------------------


def test_try_register_manager_already_registered() -> None:
    """Returns True immediately when the manager is already in APPLIERS."""
    from repo_ops_upgrade import registry

    # Fake that 'uv' is already in APPLIERS
    original = dict(registry.APPLIERS)
    try:
        registry.APPLIERS["uv"] = lambda ws: None
        result = _try_register_manager("uv")
        assert result is True
    finally:
        registry.APPLIERS.clear()
        registry.APPLIERS.update(original)


def test_try_register_manager_import_error() -> None:
    """ImportError from the ecosystem package → returns False gracefully."""
    from repo_ops_upgrade import registry

    # Ensure the manager is NOT in APPLIERS so the import path is attempted
    original = dict(registry.APPLIERS)
    try:
        registry.APPLIERS.pop("totally_fake_manager", None)
        result = _try_register_manager("totally_fake_manager")
        assert result is False  # unknown manager → early False return
    finally:
        registry.APPLIERS.clear()
        registry.APPLIERS.update(original)


def test_try_register_manager_unknown_returns_false() -> None:
    """A manager name not in the dispatch table → returns False."""
    from repo_ops_upgrade import registry

    original = dict(registry.APPLIERS)
    try:
        registry.APPLIERS.pop("never_heard_of_this", None)
        result = _try_register_manager("never_heard_of_this")
        assert result is False
    finally:
        registry.APPLIERS.clear()
        registry.APPLIERS.update(original)


def test_try_register_manager_real_registration_path() -> None:
    """Exercises the real import + registry.register path for two managers.

    Unlike the tests above, ``_try_register_manager`` itself is not patched
    here — this fails if a manager's module path can no longer be resolved,
    the exact gap that let the per-manager shims be deleted unnoticed.
    """
    from repo_ops_go.go import apply_go, check_go
    from repo_ops_js_npm.npm import apply_npm, check_npm
    from repo_ops_upgrade import registry

    original_checkers = dict(registry.CHECKERS)
    original_appliers = dict(registry.APPLIERS)
    try:
        for manager in ("npm", "go"):
            registry.CHECKERS.pop(manager, None)
            registry.APPLIERS.pop(manager, None)

        assert _try_register_manager("npm") is True
        assert registry.CHECKERS["npm"] is check_npm
        assert registry.APPLIERS["npm"] is apply_npm

        assert _try_register_manager("go") is True
        assert registry.CHECKERS["go"] is check_go
        assert registry.APPLIERS["go"] is apply_go
    finally:
        registry.CHECKERS.clear()
        registry.CHECKERS.update(original_checkers)
        registry.APPLIERS.clear()
        registry.APPLIERS.update(original_appliers)


def test_try_register_manager_missing_package_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A genuinely missing manager package logs a warning instead of vanishing silently."""
    from repo_ops_upgrade import registry

    original_appliers = dict(registry.APPLIERS)
    registry.APPLIERS.pop("npm", None)
    try:
        with (
            patch.dict("sys.modules", {"repo_ops_js_npm.npm": None}),
            caplog.at_level(logging.WARNING),
        ):
            result = _try_register_manager("npm")
        assert result is False
        assert "npm" in caplog.text
    finally:
        registry.APPLIERS.clear()
        registry.APPLIERS.update(original_appliers)


# ---------------------------------------------------------------------------
# apply_remediations adaptor
# ---------------------------------------------------------------------------


def _make_item(
    package: str = "requests",
    safe_version: str = "2.31.0",
    ecosystem: str = "py-poetry",
    workspace: str = "/ws",
) -> RemediationItem:
    return RemediationItem(
        package=package,
        safe_version=safe_version,
        ecosystem=ecosystem,
        workspace=workspace,
    )


def _mock_upgrade_result(
    status: str = "ok", changes: int = 0, error: str = ""
) -> MagicMock:
    """Build a mock WorkspaceResult (upgrade model) for process_workspace."""
    result = MagicMock()
    result.status = status
    result.error = error or None
    result.changes = [MagicMock()] * changes
    return result


def test_apply_remediations_delegates_to_process_workspace(
    tmp_path: Path,
) -> None:
    """apply_remediations calls orchestration.process_workspace for each workspace."""
    items = [_make_item(workspace=str(tmp_path))]
    mock_final = _mock_upgrade_result("ok", changes=1)

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            return_value=mock_final,
        ) as mock_pw,
    ):
        outcomes = apply_remediations(items)

    assert mock_pw.call_count == 1  # called once per unique workspace
    assert len(outcomes) == 1
    assert outcomes[0].status == "ok"
    assert outcomes[0].changes_count == 1
    assert mock_pw.call_args.kwargs["do_verify"] is True


def test_apply_remediations_deduplicates_workspaces(
    tmp_path: Path,
) -> None:
    """Multiple RemediationItems for the same workspace → single apply call."""
    ws = str(tmp_path)
    items = [
        _make_item("requests", workspace=ws),
        _make_item("Pillow", workspace=ws),
    ]
    mock_final = _mock_upgrade_result("ok")

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            return_value=mock_final,
        ) as mock_pw,
    ):
        outcomes = apply_remediations(items)

    # Only one call despite two items pointing to the same workspace
    assert mock_pw.call_count == 1
    assert len(outcomes) == 1


def test_apply_remediations_skip_when_manager_unavailable(
    tmp_path: Path,
) -> None:
    """When the manager package is unavailable → outcome.status == 'skip'."""
    items = [_make_item(workspace=str(tmp_path))]

    with patch("repo_ops_audit.audit._try_register_manager", return_value=False):
        outcomes = apply_remediations(items)

    assert len(outcomes) == 1
    assert outcomes[0].status == "skip"
    assert "not available" in outcomes[0].error


def test_apply_remediations_fail_on_exception(tmp_path: Path) -> None:
    """Exception from process_workspace → outcome.status == 'fail'."""
    items = [_make_item(workspace=str(tmp_path))]

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            side_effect=RuntimeError("apply failed for test"),
        ),
    ):
        outcomes = apply_remediations(items)

    assert outcomes[0].status == "fail"
    assert "apply failed for test" in outcomes[0].error


def test_apply_remediations_passes_allow_dirty(
    tmp_path: Path,
) -> None:
    """allow_dirty=True is forwarded to process_workspace."""
    items = [_make_item(workspace=str(tmp_path))]
    mock_final = _mock_upgrade_result("ok")

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            return_value=mock_final,
        ) as mock_pw,
    ):
        apply_remediations(items, allow_dirty=True)

    call_kwargs = mock_pw.call_args.kwargs
    assert call_kwargs.get("allow_dirty") is True


def test_apply_remediations_can_disable_verification(
    tmp_path: Path,
) -> None:
    """do_verify=False is forwarded as the explicit escape hatch."""
    items = [_make_item(workspace=str(tmp_path))]
    mock_final = _mock_upgrade_result("ok")

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            return_value=mock_final,
        ) as mock_pw,
    ):
        apply_remediations(items, do_verify=False)

    assert mock_pw.call_args.kwargs["do_verify"] is False


def test_apply_remediations_detects_node_manager(
    tmp_path: Path,
) -> None:
    """For node ecosystem, the actual node manager is detected from the workspace."""
    # Simulate a pnpm workspace
    (tmp_path / "pnpm-lock.yaml").touch()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    items = [_make_item(ecosystem="node", workspace=str(tmp_path))]
    mock_final = _mock_upgrade_result("ok")

    captured_ws = []

    def _fake_process_workspace(result, **kwargs):
        captured_ws.append(result.workspace)
        return mock_final

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            side_effect=_fake_process_workspace,
        ),
    ):
        apply_remediations(items)

    assert len(captured_ws) == 1
    # The detected manager should be "pnpm"
    assert captured_ws[0].manager == "pnpm"


# ---------------------------------------------------------------------------
# CLI: --fix flag
# ---------------------------------------------------------------------------


def test_audit_parser_fix_default_off() -> None:
    parser = build_audit_parser()
    args = parser.parse_args([])
    assert args.fix is False


def test_audit_parser_fix_flag() -> None:
    parser = build_audit_parser()
    args = parser.parse_args(["--fix"])
    assert args.fix is True


def test_audit_parser_no_verify_flag() -> None:
    parser = build_audit_parser()
    assert parser.parse_args([]).no_verify is False
    assert parser.parse_args(["--no-verify"]).no_verify is True


def test_audit_parser_allow_dirty_default_off() -> None:
    parser = build_audit_parser()
    args = parser.parse_args([])
    assert args.allow_dirty is False


def test_fix_not_called_without_flag(tmp_path: Path) -> None:
    """Without --fix, apply_remediations is NOT called (detect-only default)."""
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.apply_remediations") as mock_apply,
    ):
        rc = main([str(tmp_path)])
    assert rc == 0
    mock_apply.assert_not_called()


def test_fix_called_when_flag_passed_and_remediations_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--fix calls apply_remediations when remediations are present."""
    pip_audit_out = json.dumps(
        [
            {
                "name": "Pillow",
                "version": "9.0.0",
                "vulns": [
                    {
                        "id": "GHSA-001",
                        "aliases": ["CVE-2023-001"],
                        "fix_versions": ["9.1.0"],
                    }
                ],
            }
        ]
    )

    def _mock_run(cmd, cwd):
        # Check --version FIRST: both the probe (`pip-audit --version`) and the
        # real audit call (`pip-audit --format json ...`) contain the literal
        # "pip-audit" list element, so the version probe must be disambiguated
        # before the broader "pip-audit" membership check.
        if "--version" in cmd:
            return 0, "pip-audit 2.7.0", ""
        if "pip-audit" in cmd:
            return 0, pip_audit_out, ""
        return 0, "", ""

    mock_outcomes = [ApplyOutcome(workspace="/ws", status="ok", changes_count=1)]

    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={"/ws": "/ws"}),
        patch(
            "repo_ops_audit.audit._detect_workspaces",
            return_value=[("/ws", "py-uv", "uv")],
        ),
        patch("repo_ops_core.git.repo_label", return_value="ws"),
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_audit.audit._run", side_effect=_mock_run),
        patch(
            "repo_ops_audit.audit.apply_remediations", return_value=mock_outcomes
        ) as mock_apply,
    ):
        main(["--fix", "--fail-on", "never", str(tmp_path)])

    mock_apply.assert_called_once()
    assert mock_apply.call_args.kwargs["do_verify"] is True
    out = capsys.readouterr().out
    assert "Fix apply" in out


def test_no_verify_flag_reaches_apply(
    tmp_path: Path,
) -> None:
    """--no-verify disables post-apply verification for --fix."""
    items = [_make_item(workspace=str(tmp_path))]
    with (
        patch("repo_ops_audit.audit.run_audit", return_value=[]),
        patch(
            "repo_ops_audit.audit.collect_all_remediations",
            return_value=items,
        ),
        patch("repo_ops_audit.audit.apply_remediations") as mock_apply,
    ):
        main(["--fix", "--no-verify", "--fail-on", "never", str(tmp_path)])

    assert mock_apply.call_args.kwargs["do_verify"] is False


def test_fix_json_output_includes_apply_outcomes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--fix --json includes 'apply_outcomes' key in the JSON payload."""
    pip_audit_out = json.dumps(
        [
            {
                "name": "Pillow",
                "version": "9.0.0",
                "vulns": [
                    {
                        "id": "GHSA-001",
                        "aliases": ["CVE-2023-001"],
                        "fix_versions": ["9.1.0"],
                    }
                ],
            }
        ]
    )

    def _mock_run(cmd, cwd):
        # Check --version FIRST: both the probe (`pip-audit --version`) and the
        # real audit call (`pip-audit --format json ...`) contain the literal
        # "pip-audit" list element, so the version probe must be disambiguated
        # before the broader "pip-audit" membership check.
        if "--version" in cmd:
            return 0, "pip-audit 2.7.0", ""
        if "pip-audit" in cmd:
            return 0, pip_audit_out, ""
        return 0, "", ""

    mock_outcomes = [ApplyOutcome(workspace="/ws", status="ok", changes_count=1)]

    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={"/ws": "/ws"}),
        patch(
            "repo_ops_audit.audit._detect_workspaces",
            return_value=[("/ws", "py-uv", "uv")],
        ),
        patch("repo_ops_core.git.repo_label", return_value="ws"),
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_audit.audit._run", side_effect=_mock_run),
        patch("repo_ops_audit.audit.apply_remediations", return_value=mock_outcomes),
    ):
        main(["--fix", "--json", "--fail-on", "never", str(tmp_path)])

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "apply_outcomes" in payload
    assert len(payload["apply_outcomes"]) == 1
    assert payload["apply_outcomes"][0]["status"] == "ok"


def test_default_is_detect_only_without_fix(tmp_path: Path) -> None:
    """Baseline: audit without --fix is read-only (no apply_remediations call)."""
    with (
        patch("repo_ops_audit.audit.discover_repos", return_value={}),
        patch("repo_ops_audit.audit.apply_remediations") as mock_apply,
    ):
        rc = main(["--fix-plan", str(tmp_path)])
    assert rc == 0
    mock_apply.assert_not_called()
