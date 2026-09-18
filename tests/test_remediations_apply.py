"""Remediation plan vs apply reporting."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core import process
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade_remediations_js import remediations


def _sample_workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="test",
    )


def test_run_remediations_plan_vs_apply_detail(tmp_path: Path) -> None:
    ws = _sample_workspace(tmp_path)
    result = WorkspaceResult(workspace=ws)
    fake = remediations.RemediationHook(
        "playwright_browsers",
        lambda _ws, _ctx: True,
        lambda ws, _ctx: [f"REMEDIATE playwright: would run {ws.manager} exec playwright install"],
        lambda ws, _ctx: (True, f"{ws.manager} exec playwright install (ok)"),
    )
    with patch.object(remediations, "REMEDIATIONS", [fake]):
        remediations.run_remediations(result, apply=False)
    assert result.remediations[0]["status"] == "pending"
    assert "would run" in result.remediations[0]["detail"]

    result2 = WorkspaceResult(workspace=ws)
    with patch.object(remediations, "REMEDIATIONS", [fake]):
        remediations.run_remediations(result2, apply=True)
    assert result2.remediations[0]["status"] == "applied"
    assert "would run" not in result2.remediations[0]["detail"]
    assert "(ok)" in result2.remediations[0]["detail"]


def test_verbose_flag_observed_live(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """process.VERBOSE rebind is visible to run_remediations.

    A by-value import of VERBOSE would freeze it at False; the module-level
    reference fix (process.VERBOSE) must read the live global.
    """
    ws = _sample_workspace(tmp_path)
    result = WorkspaceResult(workspace=ws)
    fake = remediations.RemediationHook(
        "biome_schema",
        lambda _ws, _ctx: True,
        lambda _ws, _ctx: [],
        lambda _ws, _ctx: (True, "ok"),
    )
    process.set_verbose(True)
    try:
        with patch.object(remediations, "REMEDIATIONS", [fake]):
            remediations.run_remediations(result, apply=True)
    finally:
        process.set_verbose(False)

    captured = capsys.readouterr()
    assert "remediation biome_schema: detect=True" in captured.err


def test_find_biome_configs_expands_glob_pattern(tmp_path: Path) -> None:
    """_find_biome_configs expands workspace patterns containing '*' (e.g. packages/*)."""
    # Write a root package.json with workspaces: ["packages/*"]
    pkg = {"workspaces": ["packages/*"]}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    # Create two package dirs, one with biome.json and one without
    (tmp_path / "packages" / "app").mkdir(parents=True)
    (tmp_path / "packages" / "lib").mkdir(parents=True)
    (tmp_path / "packages" / "app" / "biome.json").write_text(
        '{"$schema":"https://biomejs.dev/schemas/1.5.0/schema.json"}'
    )
    found = remediations._find_biome_configs(tmp_path)
    paths = [p.parent.name for p in found]
    assert "app" in paths  # glob-expanded member with biome.json is discovered
    assert "lib" not in paths  # member without biome.json is not included


def test_detect_playwright_peer_and_optional_deps(tmp_path: Path) -> None:
    """detect_playwright finds @playwright/test in peerDependencies and optionalDependencies.

    The detect function returns True when the package is present in deps AND
    _playwright_missing_browsers signals browsers are missing (simulated here).
    The key invariant under test: packages in peer/optional sections are included
    in the dep lookup — without the fix those sections were invisible and the
    function returned False before ever reaching the browser check.
    """
    from repo_ops_models import Workspace

    def _ws(path: Path) -> Workspace:
        return Workspace(path=path, ecosystem="node", manager="bun", git_repo=path, label="t")

    # apply=True so the browser dry-run path is eligible (plan mode skips it).
    ctx: dict = {"changes": [], "apply": True}

    # peerDependencies — package found → browser check reached → True
    peer_dir = tmp_path / "peer"
    peer_dir.mkdir()
    (peer_dir / "package.json").write_text(
        json.dumps({"peerDependencies": {"@playwright/test": "^1.40.0"}})
    )
    with patch.object(remediations, "_playwright_missing_browsers", return_value=True):
        assert remediations.detect_playwright(_ws(peer_dir), ctx) is True

    # optionalDependencies — package found → browser check reached → True
    opt_dir = tmp_path / "opt"
    opt_dir.mkdir()
    (opt_dir / "package.json").write_text(
        json.dumps({"optionalDependencies": {"@playwright/test": "^1.40.0"}})
    )
    with patch.object(remediations, "_playwright_missing_browsers", return_value=True):
        assert remediations.detect_playwright(_ws(opt_dir), ctx) is True

    # neither section — must exit before browser check (return_value irrelevant)
    none_dir = tmp_path / "none"
    none_dir.mkdir()
    (none_dir / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}))
    with patch.object(remediations, "_playwright_missing_browsers", return_value=True):
        assert remediations.detect_playwright(_ws(none_dir), ctx) is False


def test_detect_playwright_skips_browser_probe_in_plan_mode(tmp_path: Path) -> None:
    """Plan mode must not shell out to playwright install --dry-run."""
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"@playwright/test": "^1.40.0"}})
    )
    ws = _sample_workspace(tmp_path)
    ctx: dict = {"changes": [], "apply": False}
    with patch.object(remediations, "_playwright_missing_browsers") as mock_browsers:
        assert remediations.detect_playwright(ws, ctx) is False
        mock_browsers.assert_not_called()


def test_js_detect_requires_node_ecosystem(tmp_path: Path) -> None:
    """JS detect hooks are no-ops for non-node ecosystems even with package.json."""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {"@biomejs/biome": "1.5.0", "@playwright/test": "1"},
                "scripts": {"generate:proto": "buf generate"},
                "packageManager": "pnpm@8.0.0",
            }
        )
    )
    (tmp_path / "biome.json").write_text(
        json.dumps({"$schema": "https://biomejs.dev/schemas/1.4.0/schema.json"})
    )
    go_ws = Workspace(
        path=tmp_path,
        ecosystem="go",
        manager="go",
        git_repo=tmp_path,
        label="test",
    )
    ctx: dict = {
        "changes": [type("C", (), {"name": "@biomejs/biome", "from_ver": "1", "to": "2"})()],
        "apply": True,
    }
    assert remediations.detect_biome(go_ws, ctx) is False
    assert remediations.detect_playwright(go_ws, ctx) is False
    assert remediations.detect_codegen(go_ws, ctx) is False
    assert remediations.detect_pm_pin(go_ws, ctx) is False


def test_apply_biome_success(tmp_path: Path, make_proc) -> None:
    (tmp_path / "package.json").write_text(json.dumps({}))
    (tmp_path / "biome.json").write_text("{}")
    ws = _sample_workspace(tmp_path)
    with patch(
        "repo_ops_upgrade_remediations_js.remediations.run",
        return_value=make_proc(0),
    ) as mock_run:
        ok, detail = remediations.apply_biome(ws, {})
    assert ok is True
    assert "(ok)" in detail
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "bunx"
    assert "biome" in cmd and "migrate" in cmd and "--write" in cmd


def test_apply_playwright_success(tmp_path: Path, make_proc) -> None:
    ws = _sample_workspace(tmp_path)
    with patch(
        "repo_ops_upgrade_remediations_js.remediations.run",
        return_value=make_proc(0),
    ) as mock_run:
        ok, detail = remediations.apply_playwright(ws, {})
    assert ok is True
    assert "playwright install" in detail
    assert "(ok)" in detail
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "bunx"
    assert "playwright" in cmd and "install" in cmd


def test_apply_codegen_success(tmp_path: Path, make_proc) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"generate:proto": "buf generate"}})
    )
    ws = _sample_workspace(tmp_path)
    with patch(
        "repo_ops_upgrade_remediations_js.remediations.run",
        return_value=make_proc(0),
    ) as mock_run:
        ok, detail = remediations.apply_codegen(ws, {})
    assert ok is True
    assert "(ok)" in detail
    assert mock_run.call_count == 1
    assert "generate:proto" in mock_run.call_args[0][0]


def test_manifest_format_formats_only_rewritten_manifests(tmp_path: Path, make_proc) -> None:
    (tmp_path / "biome.json").write_text("{}")
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin" / "biome").write_text("")
    manifests = [
        tmp_path / "package.json",
        tmp_path / "packages" / "a" / "package.json",
        tmp_path / "packages" / "b" / "package.json",
    ]
    for p in manifests:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{ "a": 1 }\n')
    result = WorkspaceResult(workspace=_sample_workspace(tmp_path))
    result.pre_apply_snapshot = {p: p.read_bytes() for p in manifests}
    manifests[1].write_text('{\n  "a": 2\n}\n')

    hook = next(h for h in remediations.REMEDIATIONS if h.id == "manifest_format")
    with (
        patch.object(remediations, "REMEDIATIONS", [hook]),
        patch.object(remediations, "run", return_value=make_proc(0)) as mock_run,
    ):
        remediations.run_remediations(result, apply=True)
    assert result.remediations[0]["status"] == "applied"
    assert mock_run.call_args[0][0][1:] == [
        "format",
        "--write",
        "--no-errors-on-unmatched",
        str(Path("packages/a/package.json")),
    ]


def test_apply_pm_pin_success(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"packageManager": "bun@1.0.0"}, indent=2) + "\n"
    )
    ws = _sample_workspace(tmp_path)
    with patch.object(remediations, "_pm_runtime_version", return_value="1.1.0"):
        ok, detail = remediations.apply_pm_pin(ws, {})
    assert ok is True
    assert "bun@1.1.0" in detail
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["packageManager"] == "bun@1.1.0"
