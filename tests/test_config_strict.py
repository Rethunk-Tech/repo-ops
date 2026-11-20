"""An unparseable config file fails loudly instead of dropping every setting."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from repo_ops_upgrade import orchestration
from repo_ops_upgrade.run import main_apply

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
