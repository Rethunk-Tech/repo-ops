import subprocess
from pathlib import Path

from repo_ops_core.owned import filter_owned, foreign_repos
from repo_ops_models import RepoBundle
from repo_ops_upgrade.run import _drop_foreign


def _repo(path: Path, url: str | None) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if url:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
    return path


def test_foreign_repos_decided_by_origin_org_not_path(tmp_path: Path) -> None:
    ours = _repo(tmp_path / "mortar-mods", "git@github.com:Rethunk-Tech/mortar-mods.git")
    nested = _repo(
        ours / "bepinex5" / "lobbyguard", "git@github.com:LethalCompanyModding/LobbyGuard.git"
    )
    azure = _repo(tmp_path / "az", "git@ssh.dev.azure.com:v3/Rethunk/proj/repo")
    lethal = _repo(tmp_path / "lm", "https://github.com/lethalmodding/x")
    local = _repo(tmp_path / "local", None)
    other = _repo(tmp_path / "gl", "https://gitlab.com/Rethunk-Tech/x")

    got = foreign_repos([ours, nested, azure, lethal, local, other])

    assert set(got) == {nested, azure, other}
    assert got[nested].startswith("not our org")


def test_owned_orgs_config_overrides_default(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "repo-ops").mkdir()
    (tmp_path / "repo-ops" / "config.toml").write_text('owned_orgs = ["Acme"]\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ours = _repo(tmp_path / "a", "git@github.com:Rethunk-Tech/a.git")
    acme = _repo(tmp_path / "b", "git@github.com:acme/b.git")

    assert list(foreign_repos([ours, acme])) == [ours]


def test_drop_foreign_lists_skipped_not_our_org(tmp_path: Path, capsys) -> None:
    ours = _repo(tmp_path / "ours", "git@github.com:Rethunk-Tech/ours.git")
    nested = _repo(
        ours / "sub" / "lobbyguard", "git@github.com:LethalCompanyModding/LobbyGuard.git"
    )
    bundles = {
        ours: RepoBundle(git_repo=ours, label="ours", workspaces=[]),
        nested: RepoBundle(git_repo=nested, label="lg", workspaces=[]),
    }

    _drop_foreign(bundles)

    assert list(bundles) == [ours]
    assert "skipped: not our org" in capsys.readouterr().out


def test_fork_with_upstream_remote_is_skipped(tmp_path: Path, capsys) -> None:
    fork = _repo(tmp_path / "f", "git@github.com:Rethunk-Tech/f.git")
    subprocess.run(
        ["git", "-C", str(fork), "remote", "add", "upstream", "https://github.com/x/f"], check=True
    )

    assert filter_owned([fork]) == []
    assert "skipped: fork" in capsys.readouterr().out


def test_origin_org_matches_host_exactly(tmp_path: Path) -> None:
    from repo_ops_core.owned import origin_org

    cases = {
        "git@github.com:Rethunk-Tech/x.git": "rethunk-tech",
        "ssh://git@github.com/Rethunk-Tech/x": "rethunk-tech",
        "https://github.com/Rethunk-Tech/x": "rethunk-tech",
        "https://tok@github.com/Rethunk-Tech/x": "rethunk-tech",
        "https://evil.example/github.com/Rethunk-Tech/x": "",
        "git@github.com.evil:Rethunk-Tech/x": "",
        "https://github.com.evil/Rethunk-Tech/x": "",
        "https://evil.example/dev.azure.com/Rethunk/p/_git/r": "",
        "https://dev.azure.com/Rethunk/p/_git/r": "rethunk",
        "git@ssh.dev.azure.com:v3/Rethunk/p/r": "rethunk",
    }
    for i, (url, want) in enumerate(cases.items()):
        assert origin_org(_repo(tmp_path / f"r{i}", url)) == want, url
