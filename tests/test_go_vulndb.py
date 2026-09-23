from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_go import vulndb
from repo_ops_go.go import _changes_from_go_list_json, _explicit_go_get_targets
from repo_ops_models import Workspace

GRPC = "google.golang.org/grpc"
GRPC_RELEASES = ["v1.82.0", "v1.82.2", "v1.83.0", "v1.83.1", "v1.83.2", "v1.84.0"]

# GO-2026-6443 as the database publishes it: fixed on two release lines and on the
# main branch, so the newest release, v1.84.0, is still affected.
ADVISORY = {
    "id": "GO-2026-6443",
    "affected": [
        {
            "package": {"name": GRPC},
            "ranges": [
                {
                    "type": "SEMVER",
                    "events": [
                        {"introduced": "0"},
                        {"fixed": "1.82.2"},
                        {"introduced": "1.83.0"},
                        {"fixed": "1.83.2"},
                        {"introduced": "1.84.0-dev"},
                        {"fixed": "1.85.0-dev.0.20260825072537-93e31b48545e"},
                    ],
                }
            ],
        }
    ],
}


@pytest.fixture
def grpc_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = {
        f"{vulndb.VULNDB}/index/modules.json": [{"path": GRPC, "vulns": [{"id": "GO-2026-6443"}]}],
        f"{vulndb.VULNDB}/ID/GO-2026-6443.json": ADVISORY,
    }
    monkeypatch.setattr(vulndb, "_fetch_json", lambda url: docs[url])


@pytest.mark.usefixtures("grpc_advisory")
@pytest.mark.parametrize(
    ("version", "affected"),
    [
        ("v1.82.1", True),
        ("v1.82.2", False),
        ("v1.83.1", True),
        ("v1.83.2", False),
        ("v1.84.0", True),
        ("v1.85.0-dev.0.20260825072537-93e31b48545e", False),
        ("v1.85.0", False),
    ],
)
def test_is_affected_follows_every_range(version: str, affected: bool) -> None:
    assert vulndb.is_affected(GRPC, version) is affected


@pytest.mark.usefixtures("grpc_advisory")
def test_affected_latest_falls_back_to_newest_clean_release() -> None:
    target = vulndb.safe_go_target(GRPC, "v1.83.0", "v1.84.0", lambda: GRPC_RELEASES)
    assert target == "v1.83.2"


@pytest.mark.usefixtures("grpc_advisory")
def test_affected_latest_with_nothing_clean_above_current_is_skipped() -> None:
    assert vulndb.safe_go_target(GRPC, "v1.83.2", "v1.84.0", lambda: GRPC_RELEASES) is None


@pytest.mark.usefixtures("grpc_advisory")
def test_clean_proposal_is_taken_without_listing_releases() -> None:
    def released() -> list[str]:
        raise AssertionError("releases listed for a clean proposal")

    assert vulndb.safe_go_target(GRPC, "v1.83.2", "v1.85.0", released) == "v1.85.0"


def test_unreachable_database_does_not_block_upgrades(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(url: str) -> object:
        raise OSError("offline")

    monkeypatch.setattr(vulndb, "_fetch_json", unreachable)
    assert vulndb.safe_go_target(GRPC, "v1.83.2", "v1.84.0", lambda: []) == "v1.84.0"


def _fake_go(current: str) -> object:
    gomod = json.dumps({"Require": [{"Path": GRPC, "Version": current}]})
    listing = json.dumps({"Path": GRPC, "Version": current, "Update": {"Version": "v1.84.0"}})
    versions = json.dumps({"Path": GRPC, "Versions": GRPC_RELEASES})

    def fake_run(cmd: list[str], cwd: Path, timeout: float = 0, env: object = None) -> object:
        if cmd[:3] == ["go", "mod", "edit"]:
            out = gomod
        elif "-versions" in cmd:
            out = versions
        else:
            out = listing
        return type("Proc", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    return fake_run


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(path=tmp_path, ecosystem="go", manager="go", git_repo=tmp_path, label="m")


@pytest.mark.usefixtures("grpc_advisory")
def test_apply_targets_the_newest_clean_release(tmp_path: Path) -> None:
    with patch("repo_ops_go.go.run", side_effect=_fake_go("v1.83.0")):
        assert _explicit_go_get_targets(_ws(tmp_path)) == [f"{GRPC}@v1.83.2"]
        changes = _changes_from_go_list_json(_ws(tmp_path))
    assert [(c.name, c.to) for c in changes] == [(GRPC, "v1.83.2")]


@pytest.mark.usefixtures("grpc_advisory")
def test_apply_leaves_a_clean_module_off_an_affected_latest(tmp_path: Path) -> None:
    with patch("repo_ops_go.go.run", side_effect=_fake_go("v1.83.2")):
        assert _explicit_go_get_targets(_ws(tmp_path)) == []
        assert _changes_from_go_list_json(_ws(tmp_path)) == []
