"""Tests for the dependabot --scaffold / --write subcommand additions.

All filesystem operations use tmp_path; no gh/git network calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_ops_dependabot.dependabot import (
    _rel_dir,
    build_dependabot_parser,
    detect_ecosystems,
    generate_dependabot_yml,
    main,
)

# ---------------------------------------------------------------------------
# _rel_dir helper
# ---------------------------------------------------------------------------


def test_rel_dir_root(tmp_path: Path) -> None:
    assert _rel_dir(tmp_path, tmp_path) == "/"


def test_rel_dir_subdir(tmp_path: Path) -> None:
    sub = tmp_path / "packages" / "app"
    sub.mkdir(parents=True)
    assert _rel_dir(sub, tmp_path) == "/packages/app"


# ---------------------------------------------------------------------------
# detect_ecosystems
# ---------------------------------------------------------------------------


def test_detect_ecosystems_npm(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "npm" in ecos


def test_detect_ecosystems_pip(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "pip" in ecos


def test_detect_ecosystems_gomod(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\ngo 1.21\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "gomod" in ecos


def test_detect_ecosystems_cargo(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\n', encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "cargo" in ecos


def test_detect_ecosystems_github_actions(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "github-actions" in ecos


def test_detect_ecosystems_github_actions_no_yml(tmp_path: Path) -> None:
    """Workflows dir without .yml files should NOT produce github-actions entry."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "README.md").write_text("# workflows\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "github-actions" not in ecos


def test_detect_ecosystems_docker(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = [e["package-ecosystem"] for e in entries]
    assert "docker" in ecos


def test_detect_ecosystems_empty(tmp_path: Path) -> None:
    entries = detect_ecosystems(tmp_path)
    assert entries == []


def test_detect_ecosystems_multiple(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ecos = {e["package-ecosystem"] for e in entries}
    assert "npm" in ecos
    assert "gomod" in ecos
    assert "github-actions" in ecos


def test_detect_ecosystems_directory_field(tmp_path: Path) -> None:
    """github-actions entries always use directory '/'."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    ga = next(e for e in entries if e["package-ecosystem"] == "github-actions")
    assert ga["directory"] == "/"


def test_detect_ecosystems_monorepo_npm_dirs(tmp_path: Path) -> None:
    """npm detected in both root and subpackage."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    sub = tmp_path / "packages" / "app"
    sub.mkdir(parents=True)
    (sub / "package.json").write_text("{}", encoding="utf-8")
    entries = detect_ecosystems(tmp_path)
    npm_dirs = {e["directory"] for e in entries if e["package-ecosystem"] == "npm"}
    assert "/" in npm_dirs
    assert "/packages/app" in npm_dirs


# ---------------------------------------------------------------------------
# generate_dependabot_yml
# ---------------------------------------------------------------------------


def test_generate_dependabot_yml_basic() -> None:
    entries = [
        {"package-ecosystem": "npm", "directory": "/"},
        {"package-ecosystem": "github-actions", "directory": "/"},
    ]
    yml = generate_dependabot_yml(entries)
    assert "version: 2" in yml
    assert 'package-ecosystem: "npm"' in yml
    assert 'package-ecosystem: "github-actions"' in yml
    assert 'directory: "/"' in yml
    assert 'interval: "weekly"' in yml


def test_generate_dependabot_yml_custom_schedule() -> None:
    entries = [{"package-ecosystem": "gomod", "directory": "/"}]
    yml = generate_dependabot_yml(entries, schedule="daily")
    assert 'interval: "daily"' in yml


def test_generate_dependabot_yml_ends_with_newline() -> None:
    entries = [{"package-ecosystem": "cargo", "directory": "/"}]
    assert generate_dependabot_yml(entries).endswith("\n")


def test_generate_dependabot_yml_no_entries() -> None:
    yml = generate_dependabot_yml([])
    assert "version: 2" in yml
    assert "updates:" in yml
    # No ecosystem entries
    assert "package-ecosystem" not in yml


def test_generate_dependabot_yml_has_repo_ops_comment() -> None:
    yml = generate_dependabot_yml([])
    assert "repo-ops" in yml


# ---------------------------------------------------------------------------
# CLI: --scaffold dry-run
# ---------------------------------------------------------------------------


def test_dependabot_scaffold_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--scaffold without --write prints content but does not write the file."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    # Need a valid git repo for discover_repos — mock it
    from unittest.mock import patch

    with patch(
        "repo_ops_dependabot.dependabot.discover_repos",
        return_value={"c": str(tmp_path)},
    ):
        rc = main(["--scaffold", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Dry-run" in out
    assert "npm" in out
    # File must NOT be written in dry-run mode
    assert not (tmp_path / ".github" / "dependabot.yml").exists()


def test_dependabot_scaffold_write(tmp_path: Path) -> None:
    """--scaffold --write creates .github/dependabot.yml."""
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    from unittest.mock import patch

    with patch(
        "repo_ops_dependabot.dependabot.discover_repos",
        return_value={"c": str(tmp_path)},
    ):
        rc = main(["--scaffold", "--write", str(tmp_path)])
    assert rc == 0
    yml_path = tmp_path / ".github" / "dependabot.yml"
    assert yml_path.exists()
    content = yml_path.read_text()
    assert "gomod" in content
    assert "github-actions" in content


def test_dependabot_scaffold_prunes_undetected_ecosystems(tmp_path: Path) -> None:
    """Generated yml contains ONLY detected ecosystems (prune-by-regeneration)."""
    # Only npm present
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    from unittest.mock import patch

    with patch(
        "repo_ops_dependabot.dependabot.discover_repos",
        return_value={"c": str(tmp_path)},
    ):
        main(["--scaffold", "--write", str(tmp_path)])

    content = (tmp_path / ".github" / "dependabot.yml").read_text()
    assert "npm" in content
    assert "gomod" not in content
    assert "cargo" not in content


def test_dependabot_scaffold_no_repos(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty repo list → graceful message, exit 0."""
    from unittest.mock import patch

    with patch("repo_ops_dependabot.dependabot.discover_repos", return_value={}):
        rc = main(["--scaffold", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No git repositories" in out


# ---------------------------------------------------------------------------
# CLI: --scaffold parser flags
# ---------------------------------------------------------------------------


def test_dependabot_parser_scaffold_flags() -> None:
    parser = build_dependabot_parser()
    args = parser.parse_args(["--scaffold", "--write", "--schedule", "daily"])
    assert args.scaffold is True
    assert args.write is True
    assert args.schedule == "daily"


def test_dependabot_parser_scaffold_defaults() -> None:
    parser = build_dependabot_parser()
    args = parser.parse_args(["--scaffold"])
    assert args.scaffold is True
    assert args.write is False
    assert args.schedule == "weekly"


# ---------------------------------------------------------------------------
# CLI: invalid flag combinations (argparse-level validation)
# ---------------------------------------------------------------------------


def test_dependabot_write_without_scaffold_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--write without --scaffold silently no-ops; must be a hard error."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--write"])
    assert exc_info.value.code == 2
    assert "--write requires --scaffold" in capsys.readouterr().err


def test_dependabot_scaffold_with_json_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--scaffold --json silently no-ops (scaffold mode never emits JSON)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--scaffold", "--json"])
    assert exc_info.value.code == 2
    assert "--scaffold" in capsys.readouterr().err


def test_dependabot_scaffold_with_close_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--scaffold --close silently no-ops (--close is PR-scan mode only)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--scaffold", "--close"])
    assert exc_info.value.code == 2
    assert "--close" in capsys.readouterr().err
