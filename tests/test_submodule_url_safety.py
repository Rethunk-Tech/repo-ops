"""Regression coverage for untrusted `.gitmodules` URL injection (issue #139).

A `.gitmodules` file in a *scanned target repo* is untrusted content. Its
``url`` field must never reach a subprocess without a ``--`` separator (to
block leading-dash option injection) and a ``GIT_ALLOW_PROTOCOL`` guard (to
block ``ext::``/``file::``/``fd::`` transports that would otherwise let a
malicious URL like ``ext::sh -c 'touch /tmp/pwned'`` execute arbitrary
commands).

Covers:
- ``_fetch_remote_tags`` (submodules.py) → ``subprocess.run``
- ``apply_submodule_tag_advances`` (submodules.py) → ``run_git`` → the
  underlying ``subprocess.run`` in repo_ops_core.git
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.git import run_git
from repo_ops_upgrade.submodules import (
    SubmoduleLink,
    _clear_submodule_tag_cache,
    _fetch_remote_tags,
    apply_submodule_tag_advances,
)

#: Malicious URLs a hostile `.gitmodules` in a scanned repo could contain.
_EXT_TRANSPORT_URL = "ext::sh -c touch /tmp/pwned"
_LEADING_DASH_URL = "--upload-pack=touch /tmp/pwned"


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the in-process tag cache before (and after) each test."""
    _clear_submodule_tag_cache()
    yield
    _clear_submodule_tag_cache()


def _make_link(
    tmp_path: Path,
    *,
    path: str = "sub",
    url: str = _EXT_TRANSPORT_URL,
) -> SubmoduleLink:
    git_repo = (tmp_path / path).resolve()
    git_repo.mkdir(parents=True, exist_ok=True)
    return SubmoduleLink(
        path=path, git_repo=git_repo, parent=tmp_path.resolve(), url=url
    )


# ---------------------------------------------------------------------------
# _fetch_remote_tags (ls-remote)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("malicious_url", [_EXT_TRANSPORT_URL, _LEADING_DASH_URL])
def test_fetch_remote_tags_isolates_malicious_url(malicious_url: str, make_proc) -> None:
    """ls-remote argv must carry a `--` separator and GIT_ALLOW_PROTOCOL guard."""
    with patch("subprocess.run", return_value=make_proc()) as mock_run:
        _fetch_remote_tags(malicious_url)

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    kwargs = mock_run.call_args[1]

    assert argv == ["git", "ls-remote", "--tags", "--refs", "--", malicious_url]
    assert "--" in argv
    assert argv.index("--") == argv.index(malicious_url) - 1

    env = kwargs.get("env")
    assert env is not None
    assert env.get("GIT_ALLOW_PROTOCOL") == "https:ssh"


# ---------------------------------------------------------------------------
# apply_submodule_tag_advances (fetch via run_git)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("malicious_url", [_EXT_TRANSPORT_URL, _LEADING_DASH_URL])
def test_apply_fetch_isolates_malicious_url(tmp_path: Path, malicious_url: str, make_proc) -> None:
    """apply_submodule_tag_advances' fetch call must carry the same guards."""
    link = _make_link(tmp_path, url=malicious_url)

    with (
        patch(
            "repo_ops_upgrade.submodules._current_submodule_tag", return_value="v1.0.0"
        ),
        patch(
            "repo_ops_upgrade.submodules._fetch_remote_tags",
            return_value=["v1.0.0", "v1.1.0"],
        ),
        patch(
            "repo_ops_upgrade.submodules.run_git", return_value=make_proc()
        ) as mock_git,
    ):
        bumps = apply_submodule_tag_advances([link])

    assert len(bumps) == 1
    assert bumps[0].is_actionable

    fetch_call = mock_git.call_args_list[0]
    args = fetch_call[0]
    kwargs = fetch_call[1]

    assert args[1] == "fetch"
    assert "--" in args
    assert args.index("--") == args.index(malicious_url) - 1

    env = kwargs.get("env")
    assert env is not None
    assert env.get("GIT_ALLOW_PROTOCOL") == "https:ssh"


# ---------------------------------------------------------------------------
# run_git (repo_ops_core.git) — env plumbing to the real subprocess.run
# ---------------------------------------------------------------------------


def test_run_git_forwards_env_and_separator(tmp_path: Path, make_proc) -> None:
    """run_git must forward an explicit `env` through to subprocess.run."""
    with patch("repo_ops_core.git.subprocess.run", return_value=make_proc()) as mock_run:
        run_git(
            tmp_path,
            "fetch",
            "--",
            _EXT_TRANSPORT_URL,
            env={"GIT_ALLOW_PROTOCOL": "https:ssh"},
            timeout=5,
        )

    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    kwargs = mock_run.call_args[1]

    assert argv == ["git", "-C", str(tmp_path), "fetch", "--", _EXT_TRANSPORT_URL]
    assert "--" in argv

    env = kwargs.get("env")
    assert env is not None
    assert env.get("GIT_ALLOW_PROTOCOL") == "https:ssh"


def test_run_git_default_env_is_none(tmp_path: Path, make_proc) -> None:
    """Existing call sites that don't pass env keep prior subprocess.run behavior."""
    with patch("repo_ops_core.git.subprocess.run", return_value=make_proc()) as mock_run:
        run_git(tmp_path, "status", "--porcelain", timeout=5)

    kwargs = mock_run.call_args[1]
    assert kwargs.get("env") is None
