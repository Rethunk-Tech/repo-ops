"""Coverage for repo_ops_dependabot.dependabot URL parsing, gh-absent branches,
and the lockfile text-search scan."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_dependabot import dependabot
from repo_ops_lockfiles.parsers import versions_bun_lockb


def test_owner_repo_ssh_https_and_trailing_slash() -> None:
    assert dependabot._owner_repo("git@github.com:owner/repo.git") == "owner/repo"
    assert dependabot._owner_repo("https://github.com/owner/repo") == "owner/repo"
    assert dependabot._owner_repo("https://github.com/owner/repo/") == "owner/repo"
    assert dependabot._owner_repo("https://gitlab.com/owner/repo") is None


def test_gh_absent_short_circuits_all_gh_helpers() -> None:
    with patch.object(dependabot.shutil, "which", return_value=None):
        prs, err = dependabot._gh_list_dependabot_prs("owner/repo")
        assert prs == []
        assert err == "gh not on PATH"

        body, body_err = dependabot._gh_pr_body("owner/repo", 42)
        assert body == ""
        assert body_err == "gh not on PATH"

        close_err = dependabot._gh_close_pr("owner/repo", 42, "closing")
        assert close_err == "gh not on PATH"


def test_lockfile_versions_scans_package_lock_json(tmp_path: Path) -> None:
    """_lockfile_versions walks the tree and parses a real package-lock.json
    (v1-style 'dependencies' map) into a name@version set."""
    lock = {
        "name": "demo",
        "lockfileVersion": 1,
        "dependencies": {
            "foo": {"version": "1.2.3"},
            "bar": {"version": "4.5.6"},
        },
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")

    versions = dependabot._lockfile_versions(str(tmp_path))
    assert "foo@1.2.3" in versions
    assert "bar@4.5.6" in versions


def test_lockfile_resolved_maps_parser_output(tmp_path: Path) -> None:
    """_lockfile_resolved returns {name: version} from lockfile parsers."""
    npm_lock = {
        "name": "demo",
        "lockfileVersion": 1,
        "dependencies": {
            "lodash": {"version": "4.17.21"},
        },
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(npm_lock), encoding="utf-8")
    (tmp_path / "yarn.lock").write_text(
        textwrap.dedent("""\
            foo@1.0.0:
              version "2.0.0"
        """),
        encoding="utf-8",
    )

    resolved = dependabot._lockfile_resolved(str(tmp_path))

    assert resolved["lodash"] == "4.17.21"
    assert resolved["foo"] == "2.0.0"


def test_versions_bun_lockb_dumps_yarn_compatible_lockfile(tmp_path: Path) -> None:
    lockb = tmp_path / "bun.lockb"
    lockb.write_bytes(b"\x00")
    proc = MagicMock(
        returncode=0,
        stdout='foo@1.0.0:\n  version "2.1.0"\n',
        stderr="",
    )

    with patch("repo_ops_lockfiles.parsers.run", return_value=proc) as mock_run:
        versions = versions_bun_lockb(tmp_path)

    assert versions == {"foo@2.1.0"}
    mock_run.assert_called_once_with(["bun", str(lockb)], cwd=tmp_path, timeout=60)


def test_lockfile_resolved_parses_bun_lockb_without_text_lock(
    tmp_path: Path,
) -> None:
    (tmp_path / "bun.lockb").write_bytes(b"\x00")
    proc = MagicMock(
        returncode=0,
        stdout='foo@1.0.0:\n  version "2.1.0"\n',
        stderr="",
    )

    with patch("repo_ops_lockfiles.parsers.run", return_value=proc):
        resolved = dependabot._lockfile_resolved(str(tmp_path))

    assert resolved["foo"] == "2.1.0"


def test_lockfile_resolved_skips_bun_lockb_without_bun(tmp_path: Path) -> None:
    (tmp_path / "bun.lockb").write_bytes(b"\x00")

    with patch(
        "repo_ops_lockfiles.parsers.run",
        side_effect=FileNotFoundError("bun"),
    ):
        resolved = dependabot._lockfile_resolved(str(tmp_path))

    assert resolved == {}


def test_lockfile_resolved_parses_uv_lock(tmp_path: Path) -> None:
    """uv.lock [[package]] entries feed the high-confidence path (#150)."""
    (tmp_path / "uv.lock").write_text(
        textwrap.dedent("""\
            version = 1
            requires-python = ">=3.12"

            [[package]]
            name = "fastapi"
            version = "0.139.2"

            [[package]]
            name = "typing-extensions"
            version = "4.12.2"
        """),
        encoding="utf-8",
    )

    resolved = dependabot._lockfile_resolved(str(tmp_path))

    assert resolved["fastapi"] == "0.139.2"
    assert resolved["typing-extensions"] == "4.12.2"


def test_resolve_package_version_uv_lock_high_confidence(tmp_path: Path) -> None:
    """A Python package resolved from uv.lock is high-confidence (auto-close eligible)."""
    (tmp_path / "uv.lock").write_text(
        textwrap.dedent("""\
            version = 1

            [[package]]
            name = "ruff"
            version = "0.15.22"

            [[package]]
            name = "typing-extensions"
            version = "4.12.2"
        """),
        encoding="utf-8",
    )

    resolved = dependabot._lockfile_resolved(str(tmp_path))

    ver, high_conf = dependabot._resolve_package_version("ruff", resolved, set(), {})
    assert ver == "0.15.22"
    assert high_conf is True

    # PEP 503 normalization: PR titles may use underscores where uv.lock
    # stores hyphens.
    ver, high_conf = dependabot._resolve_package_version("typing_extensions", resolved, set(), {})
    assert ver == "4.12.2"
    assert high_conf is True


def test_lockfile_resolved_parses_poetry_lock(tmp_path: Path) -> None:
    """poetry.lock uses the poetry parser for both high-confidence scans."""
    poetry_lock = tmp_path / "poetry.lock"
    poetry_lock.write_text(
        textwrap.dedent("""\
            [[package]]
            name = "poetry-core"
            version = "2.1.3"
        """),
        encoding="utf-8",
    )

    with (
        patch.object(
            dependabot,
            "versions_poetry_lock",
            wraps=dependabot.versions_poetry_lock,
        ) as poetry_parser,
        patch.object(dependabot, "versions_uv_lock") as uv_parser,
    ):
        resolved = dependabot._lockfile_resolved(str(tmp_path))
        versions = dependabot._lockfile_versions(str(tmp_path))

    assert resolved["poetry-core"] == "2.1.3"
    assert versions == {"poetry-core@2.1.3"}
    assert poetry_parser.call_count == 2
    poetry_parser.assert_any_call(poetry_lock)
    uv_parser.assert_not_called()
    ver, high_conf = dependabot._resolve_package_version("poetry_core", resolved, set(), {})
    assert ver == "2.1.3"
    assert high_conf is True


def test_lockfile_resolved_parses_pdm_lock(tmp_path: Path) -> None:
    """pdm.lock [[package]] entries feed the high-confidence path."""
    (tmp_path / "pdm.lock").write_text(
        textwrap.dedent("""\
            [[package]]
            name = "pdm-backend"
            version = "2.4.3"
        """),
        encoding="utf-8",
    )

    resolved = dependabot._lockfile_resolved(str(tmp_path))

    assert resolved["pdm-backend"] == "2.4.3"
    ver, high_conf = dependabot._resolve_package_version("pdm_backend", resolved, set(), {})
    assert ver == "2.4.3"
    assert high_conf is True


def test_lockfile_resolved_parses_pipfile_lock(tmp_path: Path) -> None:
    """Pipfile.lock default and develop entries feed the high-confidence path."""
    lock = {
        "default": {"requests": {"version": "==2.32.4"}},
        "develop": {"pytest": {"version": "==8.4.1"}},
    }
    (tmp_path / "Pipfile.lock").write_text(json.dumps(lock), encoding="utf-8")

    resolved = dependabot._lockfile_resolved(str(tmp_path))

    assert resolved["requests"] == "2.32.4"
    assert resolved["pytest"] == "8.4.1"
    ver, high_conf = dependabot._resolve_package_version("requests", resolved, set(), {})
    assert ver == "2.32.4"
    assert high_conf is True
