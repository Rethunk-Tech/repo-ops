from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "extract_changelog_section.sh"


def test_extracts_dated_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    output = tmp_path / "release-notes.md"
    changelog.write_text(
        """# Changelog

## [1.2.3] - 2026-01-01

- Added the release feature.

## [1.2.2] - 2025-12-01

- Fixed an earlier issue.
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(SCRIPT), "1.2.3", str(changelog), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert output.read_text(encoding="utf-8").strip() == "- Added the release feature."


def test_missing_section_fails_closed(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    output = tmp_path / "release-notes.md"
    changelog.write_text(
        "## [1.2.3] - 2026-01-01\n\n- Existing release.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(SCRIPT), "9.9.9", str(changelog), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == ""


def test_empty_section_fails_closed(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    output = tmp_path / "release-notes.md"
    changelog.write_text(
        "## [1.2.3] - 2026-01-01\n\n   \n## [1.2.2] - 2025-12-01\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(SCRIPT), "1.2.3", str(changelog), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert output.read_text(encoding="utf-8").strip() == ""


def test_near_match_heading_fails_closed(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    output = tmp_path / "release-notes.md"
    changelog.write_text(
        "## [1x2x3]\n\n- Wrong release notes.\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(SCRIPT), "1.2.3", str(changelog), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == ""
