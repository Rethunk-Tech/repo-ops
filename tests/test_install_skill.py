"""Tests for repo-ops install-skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_ops import install_skill
from repo_ops.install_skill import (
    bundled_skill_dir,
    check_installed_skill,
    main,
    rewrite_install_links,
    validate_dest,
)

_GITHUB_BLOB = "https://github.com/Rethunk-Tech/repo-ops/blob/main/"


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


def test_install_skill_dry_run_skip_when_dest_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "repo-ops"
    dest.mkdir()
    (dest / "SKILL.md").write_text("placeholder", encoding="utf-8")
    code = main(["--dest", str(dest), "--dry-run"])
    assert code == 0
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "placeholder"
    err = capsys.readouterr().err
    assert "skip" in err
    assert str(dest) in err


def test_install_skill_force_overwrites(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    dest.mkdir()
    (dest / "SKILL.md").write_text("placeholder", encoding="utf-8")
    code = main(["--dest", str(dest), "--force"])
    assert code == 0
    text = (dest / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: repo-ops\n")


def test_install_skill_check_missing_is_clean(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    assert main(["--dest", str(dest), "--check"]) == 0
    assert not dest.exists()


def test_install_skill_check_matches_rewritten_install(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    assert main(["--dest", str(dest)]) == 0
    assert main(["--dest", str(dest), "--check"]) == 0


def test_install_skill_check_matches_symlinked_install(tmp_path: Path) -> None:
    installed = tmp_path / "installed" / "repo-ops"
    link = tmp_path / "linked" / "repo-ops"
    assert main(["--dest", str(installed)]) == 0
    link.parent.mkdir()
    link.symlink_to(installed, target_is_directory=True)

    assert check_installed_skill(bundled_skill_dir(), link)
    assert main(["--dest", str(link), "--check"]) == 0


def test_install_skill_check_reports_broken_symlink(tmp_path: Path) -> None:
    dest = tmp_path / "repo-ops"
    dest.symlink_to(tmp_path / "missing", target_is_directory=True)

    assert not check_installed_skill(bundled_skill_dir(), dest)


def test_install_skill_check_reports_broken_symlink_via_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "repo-ops"
    dest.symlink_to(tmp_path / "missing" / "repo-ops", target_is_directory=True)

    assert main(["--dest", str(dest), "--check"]) == 1
    assert "drift" in capsys.readouterr().err


def test_install_skill_check_reports_drift_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "repo-ops"
    assert main(["--dest", str(dest)]) == 0
    skill_md = dest / "SKILL.md"
    drifted = skill_md.read_text(encoding="utf-8") + "\nchanged\n"
    skill_md.write_text(drifted, encoding="utf-8")

    assert main(["--dest", str(dest), "--check"]) == 1
    assert skill_md.read_text(encoding="utf-8") == drifted
    assert "drift" in capsys.readouterr().err


def test_install_skill_home_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_skill, "resolve_home", lambda _home=None: tmp_path)
    code = main(["--target", "cursor"])
    assert code == 0
    assert (tmp_path / ".cursor" / "skills" / "repo-ops" / "SKILL.md").is_file()


@pytest.mark.parametrize("target", ["claude", "both"])
def test_install_skill_target_layouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    monkeypatch.setattr(install_skill, "resolve_home", lambda _home=None: tmp_path)
    code = main(["--target", target])
    assert code == 0
    if target in ("claude", "both"):
        assert (tmp_path / ".claude" / "skills" / "repo-ops" / "SKILL.md").is_file()
    if target in ("cursor", "both"):
        assert (tmp_path / ".cursor" / "skills" / "repo-ops" / "SKILL.md").is_file()


def test_install_skill_home_flag_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "custom-home"
    monkeypatch.delenv("REPO_OPS_HOME", raising=False)
    code = main(["--target", "cursor", "--home", str(home)])
    assert code == 0
    assert (home / ".cursor" / "skills" / "repo-ops" / "SKILL.md").is_file()


def test_install_skill_repo_ops_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "env-home"
    monkeypatch.setenv("REPO_OPS_HOME", str(home))
    code = main(["--target", "claude"])
    assert code == 0
    assert (home / ".claude" / "skills" / "repo-ops" / "SKILL.md").is_file()


def test_install_skill_rejects_unsafe_dest(tmp_path: Path) -> None:
    dest = tmp_path / "not-repo-ops"
    code = main(["--dest", str(dest), "--force"])
    assert code == 1
    assert not dest.exists()


def test_install_skill_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


# --- source-overlap guard ---


def test_validate_dest_rejects_dest_equal_to_src(tmp_path: Path) -> None:
    """dest == src: rmtree would destroy the source checkout."""
    src = tmp_path / "repo-ops"
    src.mkdir()
    with pytest.raises(ValueError, match="overlaps with the source skill directory"):
        validate_dest(src, src)


def test_validate_dest_rejects_dest_ancestor_of_src(tmp_path: Path) -> None:
    """dest is an ancestor of src: rmtree(dest) removes src recursively.

    The scenario: the user passes
    ``--dest /path/to/checkout/repo-ops --force`` where src lives inside
    that checkout dir.
    """
    # dest = tmp_path/repo-ops  (the checkout root, ends with _SKILL_DIR_NAME)
    # src  = tmp_path/repo-ops/skills/repo-ops  (bundled skill inside checkout)
    dest = tmp_path / "repo-ops"
    src = dest / "skills" / "repo-ops"
    dest.mkdir(parents=True)
    with pytest.raises(ValueError, match="overlaps with the source skill directory"):
        validate_dest(dest, src)


def test_validate_dest_rejects_src_ancestor_of_dest(tmp_path: Path) -> None:
    """src is an ancestor of dest: dest is nested inside src."""
    src = tmp_path / "src-skill" / "repo-ops"
    dest = src / "nested" / "repo-ops"
    src.mkdir(parents=True)
    with pytest.raises(ValueError, match="overlaps with the source skill directory"):
        validate_dest(dest, src)


def test_validate_dest_allows_unrelated_dest(tmp_path: Path) -> None:
    """A legitimate --dest with no overlap must not raise."""
    src = tmp_path / "a" / "repo-ops"
    dest = tmp_path / "b" / "repo-ops"
    # Should not raise — unrelated paths, same final component.
    validate_dest(dest, src)


# --- skill bundle: REFERENCE.md, not AGENTS.md ---

_CONTRIBUTOR_MARKERS = ("PyPI publish", "Cutting a release")


def test_bundled_skill_has_reference_md_not_agents_md() -> None:
    """Skill dir ships operator REFERENCE.md; checkout AGENTS.md stays at root."""
    skill = bundled_skill_dir()
    assert (skill / "REFERENCE.md").is_file()
    assert not (skill / "AGENTS.md").exists()
    text = (skill / "REFERENCE.md").read_text(encoding="utf-8")
    for marker in _CONTRIBUTOR_MARKERS:
        assert marker not in text, f"{marker!r} belongs in checkout AGENTS.md"


def test_install_skill_includes_reference_md(tmp_path: Path) -> None:
    """install-skill copytree must deliver REFERENCE.md, not AGENTS.md."""
    dest = tmp_path / "skills" / "repo-ops"
    code = main(["--dest", str(dest), "--target", "cursor"])
    assert code == 0
    assert (dest / "REFERENCE.md").is_file()
    assert not (dest / "AGENTS.md").exists()


def test_rewrite_install_links_rewrites_relative_doc_paths() -> None:
    sample = (
        "[HUMANS.md](HUMANS.md) "
        "[usage](docs/usage.md#anchor) "
        "[core](src/repo_ops_core/constants.py) "
        "[keep](CONTRIBUTING.md)"
    )
    out = rewrite_install_links(sample)
    assert f"]({_GITHUB_BLOB}HUMANS.md)" in out
    assert f"]({_GITHUB_BLOB}docs/usage.md#anchor)" in out
    assert f"]({_GITHUB_BLOB}src/repo_ops_core/constants.py)" in out
    assert "[keep](CONTRIBUTING.md)" in out


def test_install_skill_rewrites_reference_links_on_install(tmp_path: Path) -> None:
    dest = tmp_path / "skills" / "repo-ops"
    code = main(["--dest", str(dest), "--target", "cursor"])
    assert code == 0
    reference = (dest / "REFERENCE.md").read_text(encoding="utf-8")
    assert f"]({_GITHUB_BLOB}HUMANS.md)" in reference
    assert f"]({_GITHUB_BLOB}docs/usage.md" in reference
    assert f"]({_GITHUB_BLOB}src/repo_ops_core/constants.py)" in reference
    for marker in _CONTRIBUTOR_MARKERS:
        assert marker not in reference
