"""yarn latest-mode behind-after-up surfacing (issue #110).

After ``yarn up -R '*'``, peer/group-constrained direct deps can be left below
their newest published version. repo-ops now reports those as ``behind_latest``
instead of silently omitting them.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_js_yarn.yarn import apply_yarn, behind_after_up
from repo_ops_models import Change, Workspace

_PKG_JSON = json.dumps(
    {"dependencies": {"@sentry/nextjs": "^10.55.0", "next": "^16.2.7"}}
)
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
        stdout = (
            json.dumps({"version": ver, "dist-tags": {"latest": ver}}) if ver else ""
        )
        return MagicMock(returncode=0, stdout=stdout, stderr="")
    return MagicMock(returncode=0, stdout="", stderr="")


def test_behind_after_up_reports_packages_left_behind(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_UPDATE_LATEST", "1")
    with patch("repo_ops_js_yarn.yarn.run", side_effect=_info_run):
        behind = behind_after_up(tmp_path)
    assert behind == [Change("@sentry/nextjs", "10.55.0", "10.56.0")]


def test_behind_after_up_disabled_in_conservative_mode(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    run_mock = MagicMock()
    with patch("repo_ops_js_yarn.yarn.run", run_mock):
        behind = behind_after_up(tmp_path)
    assert behind == []
    run_mock.assert_not_called()  # no registry lookups outside latest mode


def test_behind_after_up_resilient_to_info_failure(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_UPDATE_LATEST", "1")
    fail = MagicMock(returncode=1, stdout="", stderr="ENOTFOUND")
    with patch("repo_ops_js_yarn.yarn.run", return_value=fail):
        behind = behind_after_up(tmp_path)
    assert behind == []  # info failure skips the package, never raises


def test_apply_yarn_returns_behind_in_latest_mode(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_YARN_UPDATE_LATEST", "1")
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
