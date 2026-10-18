from pathlib import Path

from repo_ops_models import Change
from repo_ops_upgrade.soup import sync_soup_versions

TABLE = """| Package | Version | Licence |
| --- | --- | --- |
| `next` | `16.3.0` | `MIT` |
| `zod` | `4.6.5` | `MIT` |
"""


def _repo(tmp_path: Path, config: str = '[soup]\npath = "docs/SOUP.md"\n') -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/SOUP.md").write_text(TABLE)
    (tmp_path / ".repo-ops.toml").write_text(config)
    return tmp_path


def test_bumped_row_version_cell_is_rewritten(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert sync_soup_versions(repo, [Change("next", "^16.3.0", "^16.4.0")])
    assert (repo / "docs/SOUP.md").read_text() == TABLE.replace("16.3.0", "16.4.0")


def test_package_absent_from_table_changes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert not sync_soup_versions(repo, [Change("left-pad", "1.0.0", "1.1.0")])
    assert (repo / "docs/SOUP.md").read_text() == TABLE


def test_not_configured_is_a_no_op(tmp_path: Path) -> None:
    repo = _repo(tmp_path, config="")
    assert not sync_soup_versions(repo, [Change("next", "16.3.0", "16.4.0")])
    assert (repo / "docs/SOUP.md").read_text() == TABLE


def test_soup_path_outside_repo_is_not_written(tmp_path: Path) -> None:
    victim = tmp_path / "outside.md"
    victim.write_text(TABLE)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repo-ops.toml").write_text(f'[soup]\npath = "{victim}"\n')
    assert not sync_soup_versions(repo, [Change("next", "16.3.0", "16.4.0")])
    assert victim.read_text() == TABLE


def test_version_cell_takes_the_lockfile_resolution_over_the_manifest_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "bun.lock").write_text(
        '{\n  "packages": {\n    "next": ["next@16.4.2", "", {}, "sha"],\n'
        '    "x/next": ["next@15.0.0", "", {}, "sha"],\n  },\n}\n'
    )
    assert sync_soup_versions(repo, [Change("next", "^16.3.0", "^16.4.0")])
    assert (repo / "docs/SOUP.md").read_text() == TABLE.replace("16.3.0", "16.4.2")
