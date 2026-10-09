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
