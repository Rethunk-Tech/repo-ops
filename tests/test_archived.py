import subprocess
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

from repo_ops_core.archived import (
    ArchiveScan,
    archived_message,
    gh_archived_lister,
    print_archive_report,
    scan_archived,
)


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


def _gh_only_path(tmp_path: Path, monkeypatch: MonkeyPatch, script: str | None) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    if script is not None:
        gh = bindir / "gh"
        gh.write_text(script)
        gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))


def test_gh_archived_lister_parses_name_with_owner(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _gh_only_path(
        tmp_path,
        monkeypatch,
        '#!/bin/sh\necho \'[{"nameWithOwner":"Acme/Old"}]\'\n',
    )

    assert gh_archived_lister("Acme") == {"acme/old"}


def test_gh_archived_lister_returns_none_on_errors(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _gh_only_path(tmp_path, monkeypatch, None)
    assert gh_archived_lister("Acme") is None

    _gh_only_path(tmp_path / "fail", monkeypatch, "#!/bin/sh\nexit 1\n")
    assert gh_archived_lister("Acme") is None

    _gh_only_path(tmp_path / "bad", monkeypatch, "#!/bin/sh\necho not-json\n")
    assert gh_archived_lister("Acme") is None


def test_archived_message_includes_unpushed_count(tmp_path: Path) -> None:
    repo = tmp_path / "old"
    assert archived_message(repo, 3) == (
        f"{repo}: archived upstream: delete the local clone (3 unpushed commits)"
    )


def test_print_archive_report_lists_unchecked_orgs_and_archived(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    repo = tmp_path / "old"
    print_archive_report(
        ArchiveScan(archived={repo: 2}, unchecked_orgs=["Zed", "Acme"]),
        checked=3,
    )
    out = capsys.readouterr().out
    assert "3 repo(s) scanned, 1 archived, 2 org(s) unchecked" in out
    assert "Acme, Zed" in out
    assert f"  {repo} (2 unpushed commits)" in out
