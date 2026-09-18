"""Expo SDK alignment: the installed ``expo`` package's bundledNativeModules.json caps and targets deps."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_js_bun.bun import apply_bun, check_bun
from repo_ops_models import RepoBundle, WorkspaceResult
from repo_ops_upgrade.report import build_json_payload, print_human_report

_REPO_HOLDS_CACHE = "repo_ops_core.holds._repo_holds_cached"

_BUNDLED = {
    "react-native": "0.86.3",
    "react-native-gesture-handler": "~2.32.0",
    "react-native-svg": "15.15.4",
}

_OUTDATED = {
    "expo": {"current": "57.0.22", "update": "57.0.22", "latest": "58.0.1"},
    "react-native": {"current": "0.86.2", "update": "0.86.2", "latest": "0.87.0"},
    "react-native-gesture-handler": {"current": "2.32.0", "update": "2.32.0", "latest": "3.3.0"},
    "react-native-svg": {"current": "15.15.4", "update": "15.15.4", "latest": "15.16.0"},
}


@pytest.fixture(autouse=True)
def _no_global_holds():
    with patch("repo_ops_core.holds._global_holds_cached", return_value=()):
        yield


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _installed(root: Path, name: str, version: str) -> None:
    _write(root / "node_modules" / name / "package.json", {"name": name, "version": version})


def _expo_monorepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _write(tmp_path / "package.json", {"workspaces": ["apps/*"]})
    mobile = tmp_path / "apps" / "mobile"
    _write(
        mobile / "package.json",
        {
            "dependencies": {
                "expo": "~57.0.22",
                "react-native": "0.86.2",
                "react-native-gesture-handler": "~2.32.0",
                "react-native-svg": "15.15.4",
            },
            "expo": {"install": {"exclude": ["react-native-svg"]}},
        },
    )
    _installed(tmp_path, "expo", "57.0.22")
    _write(tmp_path / "node_modules" / "expo" / "bundledNativeModules.json", _BUNDLED)
    for name, meta in _OUTDATED.items():
        if name != "expo":
            _installed(tmp_path, name, meta["current"])
    return mobile


def _fake_run(tmp_path: Path, mobile: Path, calls: list[tuple[list[str], Path]]):
    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append((list(cmd), cwd))
        if cmd[:2] == ["bun", "outdated"]:
            rows = _OUTDATED.items() if cwd == mobile else ()
            table = "".join(
                f"│ {n} │ {m['current']} │ {m['update']} │ {m['latest']} │\n" for n, m in rows
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=table, stderr="")
        for arg in cmd[2:]:
            name, sep, spec = arg.rpartition("@")
            if sep and name and (spec[:1].isdigit() or spec[:1] in ("~", "^")):
                _installed(tmp_path, name, spec.lstrip("~^"))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def test_plan_caps_bundled_aligns_below_and_leaves_excluded_free(
    tmp_path: Path, make_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    mobile = _expo_monorepo(tmp_path, monkeypatch)
    root = make_workspace(tmp_path, manager="bun")
    with patch("repo_ops_js_bun.bun.run", _fake_run(tmp_path, mobile, [])):
        changes, behind = check_bun(root)

    assert {(c.name, c.from_ver, c.to, c.member) for c in changes} == {
        ("react-native", "0.86.2", "0.86.3", "apps/mobile"),
        ("react-native-svg", "15.15.4", "15.16.0", "apps/mobile"),
    }
    assert {(c.name, c.to) for c in behind} == {
        ("expo", "58.0.1"),
        ("react-native", "0.87.0"),
        ("react-native-gesture-handler", "3.3.0"),
    }

    bundle = RepoBundle(
        git_repo=tmp_path,
        label="demo",
        workspaces=[WorkspaceResult(workspace=root, changes=changes, behind_latest=behind)],
    )
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report({tmp_path: bundle}, dry_run=True)
    out = buf.getvalue()
    assert "→ 3.3.0 (latest; held <= 2.32, expo SDK 57 bundles ~2.32.0)" in out
    assert "→ 0.86.3 (expo SDK 57 bundles 0.86.3)" in out
    assert "→ 58.0.1 (latest; held <= 57, expo SDK 57: SDK upgrades are manual)" in out

    ws_json = build_json_payload({tmp_path: bundle}, roots=[str(tmp_path)], mode="plan")["repos"][
        0
    ]["workspaces"][0]
    gesture = next(e for e in ws_json["behind_latest"] if e["name"].endswith("gesture-handler"))
    assert gesture["held"] == "2.32"
    assert gesture["hold_reason"] == "expo SDK 57 bundles ~2.32.0"
    svg = next(e for e in ws_json["changes"] if e["name"] == "react-native-svg")
    assert "hold_reason" not in svg


def test_apply_writes_bundled_spec_and_never_installs_past_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mobile = _expo_monorepo(tmp_path, monkeypatch)
    _installed(tmp_path, "react-native-gesture-handler", "3.3.0")
    calls: list[tuple[list[str], Path]] = []
    fake = _fake_run(tmp_path, mobile, calls)
    from repo_ops_models import Workspace

    ws = Workspace(path=tmp_path, ecosystem="node", manager="bun", git_repo=tmp_path, label="demo")
    with patch("repo_ops_js_bun.bun.run", fake), patch("repo_ops_js.workspaces.run", fake):
        apply_bun(ws)

    mobile_cmds = [" ".join(cmd) for cmd, cwd in calls if cwd == mobile]
    update = next(cmd for cmd, cwd in calls if cwd == mobile and cmd[:3] == ["bun", "update", "--"])
    assert set(update[3:]) == {
        "react-native@0.86.3",
        "react-native-gesture-handler@~2.32.0",
        "react-native-svg@15.16.0",
    }
    joined = "\n".join(mobile_cmds)
    for past in ("react-native@0.87.0", "gesture-handler@3.3.0", "expo@58", "expo@<="):
        assert past not in joined
    assert "bun add expo@~57.0.22" in mobile_cmds
