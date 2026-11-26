import json
import os
import re
import subprocess
from pathlib import Path

from repo_ops_core.owned import filter_owned, foreign_repos, main_owned
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


def test_adopted_fork_is_swept_and_other_foreign_repos_reach_the_summary(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / "cfg" / "repo-ops").mkdir(parents=True)
    (tmp_path / "cfg" / "repo-ops" / "config.toml").write_text(
        'owned_orgs = ["Rethunk-AI"]\nadopted = ["Rethunk-AI/toml-patch"]\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    adopted = _repo(tmp_path / "tp", "git@github.com:Rethunk-AI/toml-patch.git")
    other = _repo(tmp_path / "w", "git@github.com:Rethunk-AI/wails.git")
    for fork in (adopted, other):
        subprocess.run(
            ["git", "-C", str(fork), "remote", "add", "upstream", "https://github.com/x/y"],
            check=True,
        )
    bundles = {r: RepoBundle(git_repo=r, label=r.name, workspaces=[]) for r in (adopted, other)}

    skipped = _drop_foreign(bundles)

    assert list(bundles) == [adopted]
    assert skipped == [str(other)]

    from repo_ops_upgrade.report import build_run_summary, print_run_footer

    summary = build_run_summary(bundles, foreign_skipped=skipped)
    print_run_footer(summary=summary, failures=[], exit_code=0)
    out = capsys.readouterr().out
    assert re.search(r"foreign skipped\s+1\n", out)
    assert str(other) in out


def test_missing_owned_orgs_is_a_config_error_and_nothing_is_owned(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import pytest

    from repo_ops_core.config import ConfigError
    from repo_ops_core.owned import owned_orgs
    from repo_ops_upgrade.run import main_plan

    (Path(os.environ["XDG_CONFIG_HOME"]) / "repo-ops" / "config.toml").write_text(
        'exclude = ["x"]\n'
    )
    with pytest.raises(ConfigError, match="owned_orgs is not set"):
        owned_orgs()
    monkeypatch.chdir(tmp_path)
    assert main_plan([str(tmp_path)]) == 2
    assert "owned_orgs is not set" in capsys.readouterr().err


def test_github_fork_without_upstream_is_foreign(tmp_path: Path) -> None:
    fork = _repo(tmp_path / "wails", "git@github.com:Rethunk-Tech/wails.git")
    ours = _repo(tmp_path / "ours", "git@github.com:Rethunk-Tech/ours.git")
    azure = _repo(tmp_path / "az", "git@ssh.dev.azure.com:v3/Rethunk-Tech/proj/repo")
    asked: list[set[str]] = []

    def lookup(slugs: set[str]) -> dict[str, bool]:
        asked.append(slugs)
        return {"rethunk-tech/wails": True, "rethunk-tech/ours": False}

    got = foreign_repos([fork, ours, azure], lookup)

    assert list(got) == [fork]
    assert got[fork].startswith("fork")
    assert asked == [{"rethunk-tech/wails", "rethunk-tech/ours"}]


def test_owned_cli_json_reports_every_path(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "repo-ops").mkdir()
    (tmp_path / "repo-ops" / "config.toml").write_text(
        'owned_orgs = ["Rethunk-Tech"]\nexclude = ["skipme"]\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ours = _repo(tmp_path / "ours", "git@github.com:Rethunk-Tech/ours.git")
    skipme = _repo(tmp_path / "skipme", "git@github.com:Rethunk-Tech/s.git")
    other = _repo(tmp_path / "other", "git@github.com:Other/o.git")

    assert main_owned([str(ours), str(skipme), str(other), "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        str(ours): {"owned": True, "reason": None},
        str(skipme): {"owned": False, "reason": "excluded"},
        str(other): {"owned": False, "reason": "not our org"},
    }
