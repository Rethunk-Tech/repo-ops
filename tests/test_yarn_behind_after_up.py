"""Yarn behind-after-up surfacing.

After ``yarn up -R '*'``, peer/group-constrained direct deps can be left below
their newest published version. repo-ops now reports those as ``behind_latest``
instead of silently omitting them.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_js_yarn.yarn import (
    apply_yarn,
    behind_after_up,
    targeted_bump_behind,
)
from repo_ops_models import Change, Workspace

_PKG_JSON = json.dumps({"dependencies": {"@sentry/nextjs": "^10.55.0", "next": "^16.2.7"}})
# Berry yarn.lock: @sentry/nextjs resolved at 10.55.0 (one minor behind), next current.
_YARN_LOCK = (
    "__metadata:\n"
    "  version: 8\n"
    "\n"
    '"@sentry/nextjs@npm:^10.55.0":\n'
    "  version: 10.55.0\n"
    '  resolution: "@sentry/nextjs@npm:10.55.0"\n'
    "\n"
    '"next@npm:^16.2.7":\n'
    "  version: 16.2.7\n"
    '  resolution: "next@npm:16.2.7"\n'
)

# Latest-published per package (what `yarn npm info` would return).
_LATEST = {"@sentry/nextjs": "10.56.0", "next": "16.2.7"}


def _seed(d: Path) -> None:
    (d / "package.json").write_text(_PKG_JSON, encoding="utf-8")
    (d / "yarn.lock").write_text(_YARN_LOCK, encoding="utf-8")


def _info_run(cmd: list[str], cwd: object, timeout: object = None) -> MagicMock:
    """Fake `run`: answers `yarn npm info <pkg> --json`; OK/empty for up+install."""
    if cmd[:3] == ["yarn", "npm", "info"]:
        name = cmd[3]
        ver = _LATEST.get(name)
        stdout = json.dumps({"version": ver, "dist-tags": {"latest": ver}}) if ver else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")
    return MagicMock(returncode=0, stdout="", stderr="")


def test_behind_after_up_reports_packages_left_behind(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    with patch("repo_ops_js_yarn.yarn.run", side_effect=_info_run):
        behind = behind_after_up(tmp_path)
    assert behind == [Change("@sentry/nextjs", "10.55.0", "10.56.0")]


def test_behind_after_up_resilient_to_info_failure(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    fail = MagicMock(returncode=1, stdout="", stderr="ENOTFOUND")
    with patch("repo_ops_js_yarn.yarn.run", return_value=fail):
        behind = behind_after_up(tmp_path)
    assert behind == []  # info failure skips the package, never raises


def test_apply_yarn_returns_behind(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )
    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_info_run),
    ):
        behind = apply_yarn(ws)
    assert behind == [Change("@sentry/nextjs", "10.55.0", "10.56.0")]


# --- per-package targeted bump fallback -------------------------

_SENTRY_BEHIND = [Change("@sentry/nextjs", "10.55.0", "10.56.0")]


def _lock_with_sentry(version: str) -> str:
    return (
        "__metadata:\n"
        "  version: 8\n"
        "\n"
        '"@sentry/nextjs@npm:^10.55.0":\n'
        f"  version: {version}\n"
        f'  resolution: "@sentry/nextjs@npm:{version}"\n'
    )


def _targeted_run(tmp_path: Path, *, up_rc: int = 0, install_rc: int = 0, landed: str | None):
    """Fake `run`: a successful `yarn up <pkg>@latest` rewrites the lock to `landed`."""
    lock = tmp_path / "yarn.lock"

    def _run(cmd: list[str], cwd: object, timeout: object = None) -> MagicMock:
        if cmd[:2] == ["yarn", "up"] and len(cmd) > 2 and cmd[2].endswith("@latest"):
            if up_rc == 0 and landed is not None:
                lock.write_text(_lock_with_sentry(landed), encoding="utf-8")
            return MagicMock(returncode=up_rc, stdout="", stderr="")
        if cmd[:2] == ["yarn", "install"]:
            return MagicMock(returncode=install_rc, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return _run


def test_targeted_bump_moves_the_gap(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    with patch(
        "repo_ops_js_yarn.yarn.run",
        side_effect=_targeted_run(tmp_path, landed="10.56.0"),
    ):
        applied, behind = targeted_bump_behind(tmp_path, _SENTRY_BEHIND)
    assert applied == [Change("@sentry/nextjs", "10.55.0", "10.56.0")]
    assert behind == []  # gap moved, not just reported


def test_targeted_bump_reverts_when_install_fails(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    original_lock = (tmp_path / "yarn.lock").read_text(encoding="utf-8")
    with patch(
        "repo_ops_js_yarn.yarn.run",
        side_effect=_targeted_run(tmp_path, install_rc=1, landed="10.56.0"),
    ):
        applied, behind = targeted_bump_behind(tmp_path, _SENTRY_BEHIND)
    assert applied == []
    assert behind == _SENTRY_BEHIND  # install broke → stays behind
    assert (tmp_path / "yarn.lock").read_text(encoding="utf-8") == original_lock


def test_targeted_bump_reverts_ghost_bump(tmp_path: Path, monkeypatch) -> None:
    """`yarn up` succeeds but the resolver holds the old version → revert, stay behind."""
    _seed(tmp_path)
    with patch(
        "repo_ops_js_yarn.yarn.run",
        side_effect=_targeted_run(tmp_path, landed="10.55.0"),
    ):
        applied, behind = targeted_bump_behind(tmp_path, _SENTRY_BEHIND)
    assert applied == []  # no real advance → not kept
    assert behind == _SENTRY_BEHIND


def test_apply_yarn_targeted_bump_clears_behind(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )

    def _run(cmd: list[str], cwd: object, timeout: object = None) -> MagicMock:
        # behind_after_up still sees sentry one minor back via `yarn npm info`...
        if cmd[:3] == ["yarn", "npm", "info"]:
            return _info_run(cmd, cwd, timeout)
        # ...then the targeted `yarn up @latest` lands 10.56.0.
        if cmd[:2] == ["yarn", "up"] and len(cmd) > 2 and cmd[2].endswith("@latest"):
            (tmp_path / "yarn.lock").write_text(_lock_with_sentry("10.56.0"), encoding="utf-8")
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", side_effect=_run),
    ):
        behind = apply_yarn(ws)
    assert behind == []  # targeted fallback moved the one behind dep
