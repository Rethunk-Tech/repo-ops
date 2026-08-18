"""Tests for audit --fix advisory-safe version pinning.

Verifies:
- targeted_bump_behind in npm/pnpm/yarn uses the advisory safe_version injected
  via the module-level override dict (set by apply_remediations) instead of
  always falling back to ``@latest``.
- Normal (non-audit) targeted-bump still uses ``@latest`` when no override is set.
- apply_remediations sets the npm/pnpm/yarn version overrides before delegating
  to orchestration.process_workspace so the targeted bump lands the exact version.
- _apply_py_uv_targeted issues ``uv add <pkg>==<version>`` for Python workspaces,
  and apply_remediations dispatches py-uv items to it directly.
- _extract_min_version_from_constraint correctly parses version strings and
  cargo-audit constraint expressions.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_audit.audit import (
    RemediationItem,
    _apply_py_uv_targeted,
    _extract_min_version_from_constraint,
    apply_remediations,
)
from repo_ops_js_npm.npm import (
    clear_npm_audit_version_overrides,
    set_npm_audit_version_overrides,
)
from repo_ops_js_npm.npm import (
    targeted_bump_behind as npm_targeted_bump_behind,
)
from repo_ops_js_pnpm.pnpm import (
    clear_pnpm_audit_version_overrides,
    set_pnpm_audit_version_overrides,
)
from repo_ops_js_pnpm.pnpm import (
    targeted_bump_behind as pnpm_targeted_bump_behind,
)
from repo_ops_js_yarn.yarn import (
    clear_yarn_audit_version_overrides,
    set_yarn_audit_version_overrides,
)
from repo_ops_js_yarn.yarn import (
    targeted_bump_behind as yarn_targeted_bump_behind,
)
from repo_ops_models import Change, Workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NPM_RESOLVER = "repo_ops_lockfiles.snapshots.resolved_version_from_node_modules"


def _npm_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="npm-demo",
    )


def _pnpm_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="pnpm-demo",
    )


def _seed_npm(d: Path) -> None:
    (d / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.0"}}), encoding="utf-8"
    )
    (d / "package-lock.json").write_text("{}", encoding="utf-8")


def _seed_pnpm(d: Path) -> None:
    (d / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.0"}}), encoding="utf-8"
    )
    (d / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")


def _seed_yarn(d: Path) -> None:
    (d / "package.json").write_text(
        json.dumps({"dependencies": {"lodash": "^4.17.0"}}), encoding="utf-8"
    )
    (d / "yarn.lock").write_text("", encoding="utf-8")
    (d / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")


def _ok_run(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# _extract_min_version_from_constraint
# ---------------------------------------------------------------------------


def test_extract_exact_version_string() -> None:
    assert _extract_min_version_from_constraint("1.5.5") == "1.5.5"


def test_extract_from_geq_constraint() -> None:
    assert _extract_min_version_from_constraint(">= 1.5.5") == "1.5.5"


def test_extract_from_geq_no_space() -> None:
    assert _extract_min_version_from_constraint(">=1.5.5") == "1.5.5"


def test_extract_highest_lower_bound_from_multi_constraint() -> None:
    # When multiple ">=" bounds are present, pick the highest (most restrictive).
    assert _extract_min_version_from_constraint(">= 1.5.5, >= 1.6.0") == "1.6.0"


def test_extract_returns_none_for_unresolvable() -> None:
    assert _extract_min_version_from_constraint("unknown") is None


def test_extract_exact_with_trailing_whitespace() -> None:
    assert _extract_min_version_from_constraint("  2.0.1  ") == "2.0.1"


def test_extract_from_compound_range_takes_lower_bound() -> None:
    # ">= 1.5.5, < 2.0.0" → minimum safe is 1.5.5
    result = _extract_min_version_from_constraint(">= 1.5.5, < 2.0.0")
    assert result == "1.5.5"


# ---------------------------------------------------------------------------
# npm: targeted_bump_behind uses safe_version when override is set
# ---------------------------------------------------------------------------


def test_npm_targeted_bump_uses_safe_version_from_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an audit override is set, targeted_bump_behind uses pkg@safe_version, not @latest."""
    _seed_npm(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = _npm_ws(tmp_path)
    behind = [Change("lodash", "4.17.20", "4.17.21")]

    captured: list[list[str]] = []

    def _capture(cmd, cwd, timeout=600, env=None):
        captured.append(list(cmd))
        nm = tmp_path / "node_modules" / "lodash"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(json.dumps({"version": "4.17.21"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    set_npm_audit_version_overrides({"lodash": "4.17.21"})
    try:
        with (
            patch("repo_ops_js_npm.npm.run", side_effect=_capture),
            patch(_NPM_RESOLVER, return_value="4.17.21"),
        ):
            applied, still_behind = npm_targeted_bump_behind(ws, behind)
    finally:
        clear_npm_audit_version_overrides()

    install_cmds = [c for c in captured if "install" in c]
    assert install_cmds, "expected at least one npm install call"
    # Must use the advisory-safe version, NOT @latest.
    install_joined = " ".join(install_cmds[0])
    assert "lodash@4.17.21" in install_joined, f"expected pkg@version in: {install_joined}"
    assert "lodash@latest" not in install_joined
    assert applied == [Change("lodash", "4.17.20", "4.17.21")]
    assert still_behind == []


def test_npm_targeted_bump_uses_latest_when_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an audit override, targeted_bump_behind falls back to pkg@latest."""
    _seed_npm(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = _npm_ws(tmp_path)
    behind = [Change("lodash", "4.17.20", "4.17.21")]

    captured: list[list[str]] = []

    def _capture(cmd, cwd, timeout=600, env=None):
        captured.append(list(cmd))
        nm = tmp_path / "node_modules" / "lodash"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(json.dumps({"version": "4.17.21"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # Ensure no overrides are set (clear any residue from other tests).
    clear_npm_audit_version_overrides()
    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_capture),
        patch(_NPM_RESOLVER, return_value="4.17.21"),
    ):
        npm_targeted_bump_behind(ws, behind)

    install_cmds = [c for c in captured if "install" in c]
    assert install_cmds, "expected at least one npm install call"
    install_joined = " ".join(install_cmds[0])
    assert "lodash@latest" in install_joined, f"expected @latest in: {install_joined}"


# ---------------------------------------------------------------------------
# pnpm: targeted_bump_behind uses pnpm add pkg@version when override is set
# ---------------------------------------------------------------------------


def test_pnpm_targeted_bump_uses_pnpm_add_with_safe_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With audit override, pnpm targeted bump runs pnpm add pkg@version (not update --latest)."""
    _seed_pnpm(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = _pnpm_ws(tmp_path)
    behind = [Change("lodash", "4.17.20", "4.17.21")]

    captured: list[list[str]] = []

    def _capture(cmd, cwd, timeout=600, env=None):
        captured.append(list(cmd))
        nm = tmp_path / "node_modules" / "lodash"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(json.dumps({"version": "4.17.21"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    set_pnpm_audit_version_overrides({"lodash": "4.17.21"})
    try:
        with (
            patch("repo_ops_js_pnpm.pnpm.run", side_effect=_capture),
            patch(_NPM_RESOLVER, return_value="4.17.21"),
        ):
            applied, still_behind = pnpm_targeted_bump_behind(ws, behind)
    finally:
        clear_pnpm_audit_version_overrides()

    add_cmds = [c for c in captured if "add" in c]
    assert add_cmds, "expected at least one pnpm add call"
    add_joined = " ".join(add_cmds[0])
    assert "lodash@4.17.21" in add_joined, f"expected pkg@version in: {add_joined}"
    # Must NOT have used pnpm update --latest.
    update_cmds = [c for c in captured if "update" in c and "--latest" in c]
    assert not update_cmds, f"should not have run pnpm update --latest; cmds={captured}"
    assert applied == [Change("lodash", "4.17.20", "4.17.21")]
    assert still_behind == []


def test_pnpm_targeted_bump_uses_update_latest_when_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without audit override, pnpm targeted bump still runs pnpm update --latest."""
    _seed_pnpm(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = _pnpm_ws(tmp_path)
    behind = [Change("lodash", "4.17.20", "4.17.21")]

    captured: list[list[str]] = []

    def _capture(cmd, cwd, timeout=600, env=None):
        captured.append(list(cmd))
        nm = tmp_path / "node_modules" / "lodash"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(json.dumps({"version": "4.17.21"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    clear_pnpm_audit_version_overrides()
    with (
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_capture),
        patch(_NPM_RESOLVER, return_value="4.17.21"),
    ):
        pnpm_targeted_bump_behind(ws, behind)

    update_cmds = [c for c in captured if c[:2] == ["pnpm", "update"]]
    assert update_cmds, "expected pnpm update call"
    assert "--latest" in update_cmds[0], f"expected --latest flag; cmd={update_cmds[0]}"
    assert "lodash" in update_cmds[0]


# ---------------------------------------------------------------------------
# yarn: targeted_bump_behind uses yarn up pkg@version when override is set
# ---------------------------------------------------------------------------


def test_yarn_targeted_bump_uses_safe_version_from_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With audit override, yarn targeted bump issues yarn up pkg@safe_version."""
    _seed_yarn(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    behind = [Change("lodash", "4.17.20", "4.17.21")]

    captured: list[list[str]] = []

    # yarn targeted_bump_behind reads the lock file for version, so write a minimal one.
    (tmp_path / "yarn.lock").write_text(
        '"lodash@npm:^4.17.0":\n  version: 4.17.21\n', encoding="utf-8"
    )

    def _capture(cmd, cwd, timeout=600, env=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    set_yarn_audit_version_overrides({"lodash": "4.17.21"})
    try:
        with (
            patch("repo_ops_js_yarn.yarn.run", side_effect=_capture),
            patch(
                "repo_ops_js_yarn.yarn._resolved_versions",
                return_value={"lodash": "4.17.21"},
            ),
        ):
            yarn_targeted_bump_behind(tmp_path, behind)
    finally:
        clear_yarn_audit_version_overrides()

    up_cmds = [c for c in captured if c[:2] == ["yarn", "up"]]
    assert up_cmds, "expected yarn up call"
    up_joined = " ".join(up_cmds[0])
    assert "lodash@4.17.21" in up_joined, f"expected pkg@version; got: {up_joined}"
    assert "lodash@latest" not in up_joined


def test_yarn_targeted_bump_uses_latest_when_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without audit override, yarn targeted bump issues yarn up pkg@latest."""
    _seed_yarn(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    behind = [Change("lodash", "4.17.20", "4.17.21")]

    (tmp_path / "yarn.lock").write_text(
        '"lodash@npm:^4.17.0":\n  version: 4.17.21\n', encoding="utf-8"
    )

    captured: list[list[str]] = []

    def _capture(cmd, cwd, timeout=600, env=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    clear_yarn_audit_version_overrides()
    with (
        patch("repo_ops_js_yarn.yarn.run", side_effect=_capture),
        patch(
            "repo_ops_js_yarn.yarn._resolved_versions",
            return_value={"lodash": "4.17.21"},
        ),
    ):
        yarn_targeted_bump_behind(tmp_path, behind)

    up_cmds = [c for c in captured if c[:2] == ["yarn", "up"]]
    assert up_cmds, "expected yarn up call"
    up_joined = " ".join(up_cmds[0])
    assert "lodash@latest" in up_joined, f"expected @latest; got: {up_joined}"


# ---------------------------------------------------------------------------
# apply_remediations sets JS overrides before calling process_workspace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OverrideCase:
    ecosystem: str
    manager: str
    setup_files: dict[str, str]
    package: str
    safe_version: str
    expected_version: str  # after any manager-side normalization
    overrides_module: str


_OVERRIDE_CASES = [
    _OverrideCase(
        ecosystem="node",
        manager="npm",
        setup_files={"package.json": "{}", "package-lock.json": "{}"},
        package="lodash",
        safe_version="4.17.21",
        expected_version="4.17.21",
        overrides_module="repo_ops_js_npm.npm",
    ),
    _OverrideCase(
        ecosystem="node",
        manager="pnpm",
        setup_files={
            "package.json": "{}",
            "pnpm-lock.yaml": "lockfileVersion: '6.0'\n",
        },
        package="express",
        safe_version="4.18.2",
        expected_version="4.18.2",
        overrides_module="repo_ops_js_pnpm.pnpm",
    ),
    _OverrideCase(
        # the missing `elif manager == "bun"` branch was the reported
        # silent-skip bug -- bun must wire overrides the same as npm/pnpm.
        ecosystem="node",
        manager="bun",
        setup_files={"package.json": "{}", "bun.lock": ""},
        package="lodash",
        safe_version="4.17.21",
        expected_version="4.17.21",
        overrides_module="repo_ops_js_bun.bun",
    ),
    _OverrideCase(
        ecosystem="rust",
        manager="rust",
        setup_files={"Cargo.toml": '[package]\nname = "demo"\n'},
        package="serde",
        safe_version=">= 2.0.0",
        expected_version="2.0.0",
        overrides_module="repo_ops_rust.rust",
    ),
]


@pytest.mark.parametrize("case", _OVERRIDE_CASES, ids=lambda c: c.manager)
def test_apply_remediations_sets_manager_overrides_before_process_workspace(
    tmp_path: Path, case: _OverrideCase
) -> None:
    """apply_remediations injects <manager> version overrides so targeted_bump uses
    safe_version, and clears them again once the workspace apply finishes."""
    for name, content in case.setup_files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    items = [
        RemediationItem(
            package=case.package,
            safe_version=case.safe_version,
            ecosystem=case.ecosystem,
            workspace=str(tmp_path),
        )
    ]

    overrides_captured: list[dict[str, str]] = []

    def _fake_process_workspace(result, **kwargs):
        # Capture the override state at the moment process_workspace runs.
        overrides_mod = import_module(case.overrides_module)
        overrides_captured.append(dict(overrides_mod._AUDIT_VERSION_OVERRIDES))
        mock = MagicMock()
        mock.status = "ok"
        mock.error = None
        mock.changes = []
        return mock

    with (
        patch("repo_ops_audit.audit._try_register_manager", return_value=True),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_upgrade.orchestration.process_workspace",
            side_effect=_fake_process_workspace,
        ),
    ):
        apply_remediations(items)

    assert overrides_captured, "process_workspace was not called"
    assert overrides_captured[0].get(case.package) == case.expected_version, (
        f"expected {case.package}→{case.expected_version} in {case.manager} "
        f"overrides; got {overrides_captured[0]}"
    )

    overrides_mod = import_module(case.overrides_module)
    assert overrides_mod._AUDIT_VERSION_OVERRIDES == {}, (
        f"{case.manager} overrides should be cleared after apply; "
        f"got {overrides_mod._AUDIT_VERSION_OVERRIDES}"
    )


def test_apply_remediations_applies_deno_overrides_after_audit_fix(
    tmp_path: Path,
) -> None:
    items = [
        RemediationItem(
            package="npm:lodash",
            safe_version="4.17.21",
            ecosystem="deno",
            workspace=str(tmp_path),
        )
    ]
    audit_outcome = MagicMock(status="ok")
    helper_calls: list[tuple[Workspace, dict[str, str]]] = []

    def _capture_apply(ws: Workspace) -> None:
        from repo_ops_js.deno import _AUDIT_VERSION_OVERRIDES

        helper_calls.append((ws, dict(_AUDIT_VERSION_OVERRIDES)))

    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/deno"),
        patch(
            "repo_ops_audit.audit._apply_deno_audit",
            return_value=audit_outcome,
        ),
        patch("repo_ops_core.git.git_repo_for", return_value=tmp_path),
        patch(
            "repo_ops_js.deno.apply_deno_audit_version_overrides",
            side_effect=_capture_apply,
        ),
    ):
        outcomes = apply_remediations(items)

    assert outcomes == [audit_outcome]
    assert len(helper_calls) == 1
    ws, overrides = helper_calls[0]
    assert ws.path == tmp_path
    assert ws.ecosystem == "deno"
    assert ws.manager == "deno"
    assert overrides == {"npm:lodash": "4.17.21"}

    from repo_ops_js.deno import _AUDIT_VERSION_OVERRIDES

    assert _AUDIT_VERSION_OVERRIDES == {}


# ---------------------------------------------------------------------------
# _apply_py_uv_targeted: pins Python packages via uv add pkg==version
# ---------------------------------------------------------------------------


def test_apply_py_uv_targeted_calls_uv_add_with_exact_version() -> None:
    """_apply_py_uv_targeted issues 'uv add pkg==version' for each remediation item."""
    items = [
        RemediationItem("requests", "2.31.0", "py-uv", "/ws"),
        RemediationItem("Pillow", "9.3.0", "py-uv", "/ws"),
    ]
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd):
        captured_cmds.append(list(cmd))
        return 0, "", ""

    with patch("repo_ops_audit.audit._run", side_effect=_fake_run):
        outcome = _apply_py_uv_targeted("/ws", items)

    assert outcome.status == "ok"
    assert outcome.changes_count == 2

    cmd_strs = [" ".join(c) for c in captured_cmds]
    assert any("uv add requests==2.31.0" in s for s in cmd_strs), (
        f"expected 'uv add requests==2.31.0'; got {cmd_strs}"
    )
    assert any("uv add Pillow==9.3.0" in s for s in cmd_strs), (
        f"expected 'uv add Pillow==9.3.0'; got {cmd_strs}"
    )


def test_apply_py_uv_targeted_not_latest() -> None:
    """_apply_py_uv_targeted must NOT use 'uv sync --upgrade' or equivalent."""
    items = [RemediationItem("requests", "2.31.0", "py-uv", "/ws")]
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd):
        captured_cmds.append(list(cmd))
        return 0, "", ""

    with patch("repo_ops_audit.audit._run", side_effect=_fake_run):
        _apply_py_uv_targeted("/ws", items)

    for cmd in captured_cmds:
        cmd_str = " ".join(cmd)
        assert "sync" not in cmd_str and "--upgrade" not in cmd_str, (
            f"uv targeted bump must not run general upgrade; got: {cmd_str}"
        )


def test_apply_py_uv_targeted_returns_fail_on_nonzero_exit() -> None:
    """Non-zero uv exit → ApplyOutcome with status 'fail'."""
    items = [RemediationItem("requests", "2.31.0", "py-uv", "/ws")]

    with patch("repo_ops_audit.audit._run", return_value=(1, "", "no pyproject.toml")):
        outcome = _apply_py_uv_targeted("/ws", items)

    assert outcome.status == "fail"
    assert "requests" in outcome.error
    assert "2.31.0" in outcome.error


def test_apply_py_uv_targeted_skips_empty_safe_version() -> None:
    """Items with empty safe_version are skipped (no uv add call)."""
    items = [RemediationItem("requests", "", "py-uv", "/ws")]
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd):
        captured_cmds.append(list(cmd))
        return 0, "", ""

    with patch("repo_ops_audit.audit._run", side_effect=_fake_run):
        outcome = _apply_py_uv_targeted("/ws", items)

    assert outcome.status == "ok"
    assert outcome.changes_count == 0
    assert not captured_cmds, "no uv add should be issued for empty safe_version"


def test_apply_remediations_dispatches_py_uv_to_targeted_apply(
    tmp_path: Path,
) -> None:
    """apply_remediations routes py-uv items straight to _apply_py_uv_targeted,
    bypassing the generic uv sync --upgrade orchestration path."""
    items = [
        RemediationItem(
            package="requests",
            safe_version="2.31.0",
            ecosystem="py-uv",
            workspace=str(tmp_path),
        )
    ]
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd):
        captured_cmds.append(list(cmd))
        return 0, "", ""

    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_audit.audit._run", side_effect=_fake_run),
    ):
        outcomes = apply_remediations(items)

    assert len(outcomes) == 1
    assert outcomes[0].status == "ok"
    cmd_strs = [" ".join(c) for c in captured_cmds]
    assert any("uv add requests==2.31.0" in s for s in cmd_strs), (
        f"expected 'uv add requests==2.31.0'; got {cmd_strs}"
    )
    assert not any("sync" in s or "--upgrade" in s for s in cmd_strs), (
        f"must not fall through to the broad uv sync --upgrade path; got {cmd_strs}"
    )
