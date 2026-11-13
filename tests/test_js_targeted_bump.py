"""The shared JS behind-latest retry keeps a bump only when the version advanced."""

from __future__ import annotations

from pathlib import Path

from repo_ops_js.targeted_bump import run_targeted_bump
from repo_ops_models import Change


def test_a_prerelease_moved_to_its_release_is_kept(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}\n")
    landed = run_targeted_bump(
        None,
        tmp_path,
        Change("next", "16.2.7-canary.3", "16.2.7"),
        manager="npm",
        run_bump=lambda _name, _target: True,
        resolve_landed=lambda _name: "16.2.7",
        release_pin=lambda _mdir, _name: None,
        verify=None,
    )
    assert landed == Change("next", "16.2.7-canary.3", "16.2.7")
