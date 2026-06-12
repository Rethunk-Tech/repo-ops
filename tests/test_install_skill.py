"""Tests for repo-ops install-skill."""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_ops import install_skill
from repo_ops.install_skill import bundled_skill_dir, main, validate_dest


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


# --- source-overlap guard (issue #40) ---


def test_validate_dest_rejects_dest_equal_to_src(tmp_path: Path) -> None:
    """dest == src: rmtree would destroy the source checkout."""
    src = tmp_path / "repo-ops"
    src.mkdir()
    with pytest.raises(ValueError, match="overlaps with the source skill directory"):
        validate_dest(src, src)


def test_validate_dest_rejects_dest_ancestor_of_src(tmp_path: Path) -> None:
    """dest is an ancestor of src: rmtree(dest) removes src recursively.

    This is the exact scenario from issue #40 — the user passes
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


# --- AGENTS.md bundle drift guard ---

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ROOT_AGENTS_MD = _REPO_ROOT / "AGENTS.md"


def test_bundled_agents_md_is_byte_identical_to_root() -> None:
    """Bundled AGENTS.md must stay in sync with the root copy.

    Edit root AGENTS.md → copy to packages/repo-ops/src/repo_ops/skills/repo-ops/AGENTS.md.
    This test fails when one is edited without updating the other.
    """
    skill = bundled_skill_dir()
    bundled = skill / "AGENTS.md"
    assert bundled.is_file(), f"bundled AGENTS.md missing at {bundled}"
    assert bundled.read_bytes() == _ROOT_AGENTS_MD.read_bytes(), (
        "Bundled AGENTS.md has drifted from root AGENTS.md. "
        "Run: cp AGENTS.md packages/repo-ops/src/repo_ops/skills/repo-ops/AGENTS.md"
    )


def test_install_skill_includes_agents_md(tmp_path: Path) -> None:
    """install-skill copytree must deliver AGENTS.md to the destination."""
    dest = tmp_path / "skills" / "repo-ops"
    code = main(["--dest", str(dest), "--target", "cursor"])
    assert code == 0
    assert (dest / "AGENTS.md").is_file()
