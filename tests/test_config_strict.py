"""An unparseable config file fails loudly instead of dropping every setting."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from repo_ops_core.config import ConfigError
from repo_ops_upgrade import orchestration
from repo_ops_upgrade.run import main_apply, main_plan

BAD_TOML = '[[hold]]\npackage = "react"\nmax_version = "18"\n\n[verify]\nskip = yes\n'


def test_bad_repo_config_fails_that_repo_and_sweep_continues(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any, capsys: Any
) -> None:
    repos = {}
    for name in ("bad", "good"):
        repo = tmp_path / name
        init_git_repo(repo, commit=True)
        (repo / "package.json").write_text('{"name": "x", "packageManager": "npm@10.0.0"}\n')
        (repo / "package-lock.json").write_text("{}\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "pkg"], check=True)
        repos[name] = repo
    (repos["bad"] / ".repo-ops.toml").write_text(BAD_TOML)
    subprocess.run(["git", "-C", str(repos["bad"]), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repos["bad"]), "commit", "-qm", "cfg"], check=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    seen: list[Path] = []

    def _process(wr: Any, **_: Any) -> Any:
        seen.append(wr.workspace.git_repo)
        return wr

    monkeypatch.setattr(orchestration, "process_workspace", _process)
    rc = main_apply([str(repos["bad"]), str(repos["good"]), "--no-commit"])

    assert rc == 1
    assert seen == [repos["good"]]
    assert "unreadable .repo-ops.toml" in capsys.readouterr().err


def test_bad_global_config_exits_2_before_any_work(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    cfg = tmp_path / "xdg-config" / "repo-ops"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(BAD_TOML)
    assert main_apply([str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "config.toml" in err and "unreadable" in err


def test_bad_repo_config_is_a_failed_entry_in_report_json(
    tmp_path: Path, init_git_repo: Any, monkeypatch: Any
) -> None:
    import json

    repo = tmp_path / "bad"
    init_git_repo(repo, commit=True)
    (repo / "package.json").write_text('{"name": "x", "packageManager": "npm@10.0.0"}\n')
    (repo / "package-lock.json").write_text("{}\n")
    (repo / ".repo-ops.toml").write_text(BAD_TOML)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "pkg"], check=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    assert main_plan([str(repo)]) == 1

    (report_file,) = (tmp_path / "state" / "repo-ops" / "runs").glob("*-plan/report.json")
    report = json.loads(report_file.read_text())
    (ws,) = [w for r in report["repos"] for w in r["workspaces"]]
    assert ws["status"] == "fail" and "unreadable .repo-ops.toml" in ws["error"]
    assert report["summary"]["failure_count"] == 1


@pytest.mark.parametrize(
    "toml",
    [
        '[hold]\npackage = "react"\nmax_version = "18"\n',
        '[[hold]]\npackage = "react"\nmax-version = "18"\n',
        '[verify]\ntimeout = "120"\n',
        "verify = 3\n",
        '[[hold]]\npackage = "semver"\nmax_version = "<7"\n',
        '[[hold]]\npackage = "semver"\nmax_version = "6.x"\n',
        '[[hold]]\npackage = "semver"\nmax_version = "latest"\n',
        '[[hold]]\npackage = "semver"\nmax_version = "6"\nuntil = "2026-13-01"\n',
    ],
)
def test_malformed_hold_and_verify_values_raise_config_error(tmp_path: Path, toml: str) -> None:
    from repo_ops_core.holds import load_repo_holds
    from repo_ops_models import Workspace
    from repo_ops_upgrade.verify import _load_verify_profile

    (tmp_path / ".repo-ops.toml").write_text(toml)
    ws = Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label=".")
    checked = load_repo_holds if "hold" in toml else lambda p: _load_verify_profile(ws)
    with pytest.raises(ConfigError):
        checked(tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        'excludes = ["a"]\n',
        "[verify]\nskipp = true\n",
        '[soup]\npth = "x"\n',
        '[[hold]]\npackage = "a"\nmax_version = "1"\nmax = "2"\n',
        'owned_orgs = "Rethunk-Tech"\n',
        'adopted = "a/b"\n',
        "exclude = 3\n",
    ],
)
def test_unknown_key_or_wrong_list_type_raises(tmp_path: Path, text: str) -> None:
    from repo_ops_core.config import load_repo_config

    (tmp_path / ".repo-ops.toml").write_text(text)
    with pytest.raises(ConfigError):
        load_repo_config(tmp_path)


def test_operator_config_still_loads() -> None:
    from repo_ops_core.config import _validate

    _validate(
        Path("c"),
        {"adopted": ["a/b"], "exclude": ["x"], "verify": {"timeout": 1}, "soup": {"path": "p"}},
    )
