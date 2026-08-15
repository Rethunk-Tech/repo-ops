"""Integration test: pre-commit rev pin check via check_precommit.

Uses a real ``.pre-commit-config.yaml`` with semver rev pins and runs
``check_precommit`` against live git tag resolution.

Network: tag fetches may reach GitHub; skipped when resolution fails with a
network-style error.
"""

from __future__ import annotations

import shutil
import textwrap

import pytest

from repo_ops_models import Workspace
from repo_ops_upgrade.precommit import _clear_tag_cache, check_precommit

pytestmark = pytest.mark.integration

_PRE_COMMIT = shutil.which("pre-commit")
_GIT = shutil.which("git")

_CONFIG = textwrap.dedent("""\
    repos:
    -   repo: https://github.com/pre-commit/pre-commit-hooks
        rev: v4.5.0
        hooks:
        -   id: trailing-whitespace
""")


@pytest.mark.skipif(
    _PRE_COMMIT is None or _GIT is None,
    reason="pre-commit and git must both be on PATH",
)
def test_check_precommit_resolves_real_tags(tmp_path) -> None:
    """check_precommit returns a list without crashing against a real config."""
    _clear_tag_cache()
    (tmp_path / ".pre-commit-config.yaml").write_text(_CONFIG, encoding="utf-8")

    ws = Workspace(
        path=tmp_path,
        ecosystem="precommit",
        manager="precommit",
        git_repo=tmp_path,
        label="precommit-integ",
    )

    try:
        changes = check_precommit(ws)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if any(kw in msg for kw in ("network", "fetch", "connect", "timeout")):
            pytest.skip(f"check_precommit failed (network): {exc}")
        raise
    finally:
        _clear_tag_cache()

    assert isinstance(changes, list)
