import subprocess
from pathlib import Path

from repo_ops_core.archived import scan_archived


def _clone(path: Path, url: str) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
    return path


def test_scan_skips_archived_and_asks_once_per_org(tmp_path: Path) -> None:
    old = _clone(tmp_path / "old", "git@github.com:Acme/Old.git")
    live = _clone(tmp_path / "live", "https://github.com/Acme/live")
    local = _clone(tmp_path / "local", "/srv/git/local.git")
    calls: list[str] = []

    def lister(org: str) -> set[str] | None:
        calls.append(org)
        return {"acme/old"}

    scan = scan_archived([old, live, local], lister)

    assert list(scan.archived) == [old]
    assert calls == ["Acme"]


def test_scan_reports_unchecked_org_when_gh_fails(tmp_path: Path) -> None:
    repo = _clone(tmp_path / "r", "git@github.com:Acme/r.git")

    scan = scan_archived([repo], lambda _org: None)

    assert scan.archived == {}
    assert scan.unchecked_orgs == ["Acme"]
