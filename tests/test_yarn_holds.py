"""yarn wiring for package version-ceiling ("hold") rules."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_js_yarn.yarn import apply_yarn, check_yarn_snapshot, targeted_bump_behind
from repo_ops_models import Change, Workspace

_GLOBAL_HOLDS_CACHE = "repo_ops_js.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_js.holds._repo_holds_cached"

# check_yarn_snapshot patch targets (mirrors tests/test_yarn_upgrade_policy.py).
_COPY_PATH = "repo_ops_js_yarn.yarn._copy_yarn_workspace_to_temp"
_SNAPSHOT_PATH = "repo_ops_js_yarn.yarn.snapshot_workspace_files"
_DIFF_PATH = "repo_ops_js_yarn.yarn.diff_workspace_snapshots"
_OVERRIDES_PATH = "repo_ops_js_yarn.yarn.overrides_audit"


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="demo",
    )


def _held_rule(**overrides):
    from repo_ops_core.holds import HoldRule

    defaults = {"package": "typescript", "max_version": "6"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _lock_with_typescript(version: str) -> str:
    return (
        "__metadata:\n"
        "  version: 8\n"
        "\n"
        '"typescript@npm:^6.0.0":\n'
        f"  version: {version}\n"
        f'  resolution: "typescript@npm:{version}"\n'
    )


def _seed(
    d: Path, *, pkg_content: str | None = None, lock_version: str = "6.5.0"
) -> None:
    if pkg_content is None:
        pkg_content = json.dumps({"dependencies": {"typescript": "^6.0.0"}})
    (d / "package.json").write_text(pkg_content, encoding="utf-8")
    (d / "yarn.lock").write_text(_lock_with_typescript(lock_version), encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_repo_holds():
    with patch(_REPO_HOLDS_CACHE, return_value=()):
        yield


# ---------------------------------------------------------------------------
# (a) targeted bump uses the ceiling spec instead of latest
# ---------------------------------------------------------------------------


def test_targeted_bump_uses_hold_ceiling_instead_of_latest(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    ws = _ws(tmp_path)
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=None):
        captured_cmds.append(list(cmd))
        if cmd[:2] == ["yarn", "up"]:
            (tmp_path / "yarn.lock").write_text(
                _lock_with_typescript("6.9.0"), encoding="utf-8"
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_fake_run),
    ):
        targeted_bump_behind(tmp_path, [Change("typescript", "6.5.0", "7.0.0")], ws=ws)

    joined = [" ".join(c) for c in captured_cmds]
    assert any('typescript@"<=6"' in c or "typescript@<=6" in c for c in joined)
    assert not any("typescript@latest" in c for c in joined)


def test_targeted_bump_without_ws_keeps_latest_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """Direct callers that omit ``ws`` (pre-holds tests) keep prior behavior."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=None):
        captured_cmds.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_fake_run),
    ):
        targeted_bump_behind(tmp_path, [Change("typescript", "6.5.0", "7.0.0")])

    joined = [" ".join(c) for c in captured_cmds]
    assert any("typescript@latest" in c for c in joined)


# ---------------------------------------------------------------------------
# (b) check-time promotion skips a held-and-over-ceiling candidate
# ---------------------------------------------------------------------------


def test_check_yarn_snapshot_does_not_promote_held_package_past_ceiling(
    tmp_path: Path, monkeypatch
) -> None:
    """A behind-latest candidate blocked by a hold ceiling never lands in changes.

    ``yarn npm info`` reports latest 7.0.0 (over the ceiling); the targeted
    bump attempt installs `typescript@"<=6"`, which cannot move past the
    already-installed 6.5.0 (nothing newer satisfies the ceiling) — so the
    bump doesn't advance, and the original over-ceiling gap stays reported as
    still-behind instead of being promoted into the plan preview.
    """
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_UPDATE_LATEST", "1")
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    ws = _ws(tmp_path)

    def _fake_run(cmd, cwd, timeout=None):
        if cmd[:3] == ["yarn", "npm", "info"]:
            return MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {"version": "7.0.0", "dist-tags": {"latest": "7.0.0"}}
                ),
                stderr="",
            )
        if cmd[:2] == ["yarn", "up"] and len(cmd) > 2 and "latest" in cmd[2]:
            # Would only be reached if the hold were NOT applied.
            (tmp_path / "yarn.lock").write_text(
                _lock_with_typescript("7.0.0"), encoding="utf-8"
            )
        # Hold-ceiling install (`typescript@"<=6"`) or `yarn install`: 6.5.0
        # already satisfies <=6 and there's nothing newer under the ceiling,
        # so the lockfile is left unchanged (no advance).
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_fake_run),
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch(_COPY_PATH, return_value=tmp_path),
        patch(_SNAPSHOT_PATH, return_value={}),
        patch(_DIFF_PATH, return_value=[]),
        patch(_OVERRIDES_PATH, return_value=[]),
    ):
        changes, _overrides, behind = check_yarn_snapshot(ws)

    assert "typescript" not in {c.name for c in changes}
    assert behind == [Change("typescript", "6.5.0", "7.0.0")]


# ---------------------------------------------------------------------------
# (c) / (d) end-of-apply clamp-down (apply_yarn -> enforce_holds)
# ---------------------------------------------------------------------------


def _node_modules_typescript(tmp_path: Path, version: str) -> Path:
    nm_dir = tmp_path / "node_modules" / "typescript"
    nm_dir.mkdir(parents=True, exist_ok=True)
    (nm_dir / "package.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    return nm_dir


def test_apply_yarn_clamps_package_already_past_ceiling(
    tmp_path: Path, monkeypatch
) -> None:
    """Retroactive hold: package already on 7.x gets clamped back down.

    Simulates a hold added after typescript was installed at 7.x: `changes`
    and `behind` are both empty going into the end-of-apply safety net (no
    outdated report — the manifest range already matches what's installed),
    but the resolved node_modules version violates the newly added ceiling.
    """
    _seed(tmp_path, pkg_content=json.dumps({"dependencies": {"typescript": "^7.0.0"}}))
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    monkeypatch.delenv("REPO_OPS_YARN_TARGETED_BUMP", raising=False)
    ws = _ws(tmp_path)
    nm_dir = _node_modules_typescript(tmp_path, "7.2.0")

    def _fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["yarn", "up"] and len(cmd) > 2 and "typescript@" in cmd[2]:
            (nm_dir / "package.json").write_text(
                json.dumps({"version": "6.9.0"}), encoding="utf-8"
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_fake_run),
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
    ):
        behind = apply_yarn(ws)

    assert len(behind) == 1
    assert behind[0].name == "typescript"
    assert behind[0].from_ver == "6.9.0"
    assert behind[0].to == "7.2.0"


def test_apply_yarn_raises_when_clamp_cannot_satisfy_ceiling(
    tmp_path: Path, monkeypatch
) -> None:
    """A corrective install that still can't land under ceiling is a loud failure."""
    _seed(tmp_path, pkg_content=json.dumps({"dependencies": {"typescript": "^7.0.0"}}))
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    monkeypatch.delenv("REPO_OPS_YARN_TARGETED_BUMP", raising=False)
    ws = _ws(tmp_path)
    _node_modules_typescript(tmp_path, "7.2.0")

    def _fake_run(cmd, cwd, timeout=None):
        # Corrective install "succeeds" but node_modules is never rewritten —
        # simulates a ceiling no published version can satisfy.
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_fake_run),
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        pytest.raises(RuntimeError, match="hold"),
    ):
        apply_yarn(ws)
