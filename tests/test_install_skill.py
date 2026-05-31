"""Tests for repo-ops install-skill."""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_ops import install_skill
from repo_ops.install_skill import bundled_skill_dir, main


def test_bundled_skill_dir_contains_skill_md() -> None:
    skill = bundled_skill_dir()
    assert (skill / "SKILL.md").is_file()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: repo-ops\n")


def test_install_skill_to_dest(tmp_path: Path) -> None:
    dest = tmp_path / "skills" / "repo-ops"
    code = main(["--dest", str(dest), "--target", "cursor"])
    assert code == 0
    assert (dest / "SKILL.md").is_file()


def test_install_skill_dry_run_does_not_write(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    code = main(["--dest", str(dest), "--dry-run"])
    assert code == 0
    assert not dest.exists()


def test_install_skill_skip_without_force(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    dest.mkdir()
    (dest / "SKILL.md").write_text("placeholder", encoding="utf-8")
    code = main(["--dest", str(dest)])
    assert code == 1
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "placeholder"


def test_install_skill_force_overwrites(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    dest.mkdir()
    (dest / "SKILL.md").write_text("placeholder", encoding="utf-8")
    code = main(["--dest", str(dest), "--force"])
    assert code == 0
    text = (dest / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: repo-ops\n")


def test_install_skill_home_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install_skill, "resolve_home", lambda _home=None: tmp_path)
    code = main(["--target", "cursor"])
    assert code == 0
    assert (tmp_path / ".cursor" / "skills" / "repo-ops" / "SKILL.md").is_file()


def test_install_skill_rejects_unsafe_dest(tmp_path: Path) -> None:
    dest = tmp_path / "not-repo-ops"
    code = main(["--dest", str(dest), "--force"])
    assert code == 1
    assert not dest.exists()


def test_install_skill_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
