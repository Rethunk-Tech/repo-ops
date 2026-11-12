import subprocess
from pathlib import Path

from repo_ops_core.owned import foreign_repos
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

    assert got == {nested: "lethalcompanymodding", azure: "rethunk", other: ""}


def test_owned_orgs_config_overrides_default(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "repo-ops").mkdir()
    (tmp_path / "repo-ops" / "config.toml").write_text('owned_orgs = ["Acme"]\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ours = _repo(tmp_path / "a", "git@github.com:Rethunk-Tech/a.git")
    acme = _repo(tmp_path / "b", "git@github.com:acme/b.git")

    assert foreign_repos([ours, acme]) == {ours: "rethunk-tech"}


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
