"""Integration tests: real-``go`` env-classification via ``check_go``.

Covers two no-network paths that exercise ``classify_go_error`` end-to-end:

1. go.work directive mismatch — ``go`` directive older than a member module.
2. Missing replace target — ``replace`` points at a sibling that is absent.

Both call real ``go list``; failures happen before any module proxy fetch.
Skip the whole module when ``go`` is not on ``PATH``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from repo_ops_go.go import check_go, register_go_work_dir
from repo_ops_models import Workspace

pytestmark = pytest.mark.integration

_GO = shutil.which("go")


@pytest.mark.skipif(_GO is None, reason="go not on PATH")
def test_go_work_directive_mismatch_raises_env_error() -> None:
    """check_go raises [env] error with 'go work use' hint on go directive mismatch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        member = root / "member"
        member.mkdir()

        # go.work: go 1.21 (older than member requirement)
        (root / "go.work").write_text(
            "go 1.21\n\nuse (\n\t./member\n)\n",
            encoding="utf-8",
        )
        # member/go.mod: go 1.24 (newer than go.work directive)
        (member / "go.mod").write_text(
            "module example.com/member\n\ngo 1.24\n",
            encoding="utf-8",
        )

        ws = Workspace(
            path=member,
            ecosystem="go",
            manager="go",
            git_repo=root,
            label="go-env-test",
        )
        # Register the go.work directory so classify_go_error includes its path.
        register_go_work_dir(member, root)

        with pytest.raises(RuntimeError) as exc_info:
            check_go(ws)

        msg = str(exc_info.value)
        assert msg.startswith("[env] "), f"expected [env] prefix, got: {msg!r}"
        assert "go work use" in msg, f"expected 'go work use' hint, got: {msg!r}"


@pytest.mark.skipif(_GO is None, reason="go not on PATH")
def test_go_replace_target_missing_raises_env_error() -> None:
    """check_go raises [env] when replace points at a missing sibling go.mod.

    Mirrors ``test_classify_replace_target_no_such_file`` but through a real
    ``go list`` invocation (GOPROXY unused — failure is local filesystem).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "go.mod").write_text(
            "module example.com/replace-test\n"
            "\n"
            "go 1.22\n"
            "\n"
            "require example.com/missing v0.0.0\n"
            "\n"
            "replace example.com/missing => ../does-not-exist\n",
            encoding="utf-8",
        )

        ws = Workspace(
            path=root,
            ecosystem="go",
            manager="go",
            git_repo=root,
            label="go-replace-missing",
        )

        with pytest.raises(RuntimeError) as exc_info:
            check_go(ws)

        msg = str(exc_info.value)
        assert msg.startswith("[env] "), f"expected [env] prefix, got: {msg!r}"
        assert "replace target" in msg.lower() or "missing" in msg.lower(), (
            f"expected replace-target hint, got: {msg!r}"
        )
        assert "no such file" in msg or "does not exist" in msg, (
            f"expected missing-path stderr, got: {msg!r}"
        )
