"""Shared pytest fixtures for the top-level test suite.

Isolates ``REPO_OPS_*`` environment variables between tests. Several code
paths (notably ``repo_ops_upgrade.run``'s ``--latest`` handling) mutate
``os.environ`` directly rather than going through a fixture, and existing
tests that exercise those paths don't know to restore every var that might
get set. Left unchecked, one test's ``REPO_OPS_*`` mutation leaks into the
next test in the same pytest process (order-dependent failures that don't
reproduce in isolation).

Also isolates the persistent state store: ``process_workspace`` records
behind-latest history through ``repo_ops_core.state`` (atexit-flushed to
``$XDG_CACHE_HOME/repo-ops/state.json``), so without a per-test cache dir
any test driving it would pollute the developer's real state file.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from repo_ops_models.models import Change, Workspace

_ENV_PREFIX = "REPO_OPS_"


@pytest.fixture
def make_change() -> Any:
    """Factory fixture: build a ``Change`` for tests that only care about ``.name``.

    Same conftest/fixture-not-import rationale as ``make_workspace`` above.
    """

    def _factory(name: str, from_ver: str = "1.0.0", to: str = "2.0.0") -> Change:
        return Change(name=name, from_ver=from_ver, to=to)

    return _factory


@pytest.fixture
def make_proc() -> Any:
    """Factory fixture: a real ``subprocess.CompletedProcess`` for ``run()`` patches.

    Stands in for what ``run()`` actually returns — a hand-rolled
    ``MagicMock``/``type()`` shim only coincidentally has the right shape
    and drifts silently if the code under test starts reading another
    ``CompletedProcess`` attribute.
    """

    def _factory(
        returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)

    return _factory


@pytest.fixture
def init_git_repo() -> Any:
    """Factory fixture: ``git init`` + a throwaway committer identity.

    Same conftest/fixture-not-import rationale as ``make_workspace`` above.

    With ``commit=True``, also stages and commits *filename* and returns the
    resulting HEAD SHA; otherwise returns ``None``. The committer identity is
    always configured (even when the caller never commits) because it is
    cheap and lets a test add its own commit later without repeating it.
    """

    def _factory(
        path: Path,
        *,
        commit: bool = False,
        filename: str = "README.md",
        content: str = "init\n",
    ) -> str | None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        if not commit:
            return None
        (path / filename).write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "add", filename], cwd=path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return _factory


@pytest.fixture
def make_workspace() -> Any:
    """Factory fixture: build a ``Workspace`` with common test defaults.

    A plain module-level helper would collide at import time with the other
    ``conftest.py`` under ``tests/integration`` (both resolve to the bare
    name ``conftest``, and whichever loads last wins in ``sys.modules``) —
    so this is exposed as a fixture instead; tests take ``make_workspace``
    as a parameter rather than importing it.

    Call as ``make_workspace(tmp_path, **overrides)`` — any ``Workspace``
    field can be overridden by keyword; ``path``/``git_repo`` default to the
    first positional argument.
    """

    def _factory(root: Path, **overrides: Any) -> Workspace:
        fields: dict[str, Any] = {
            "path": root,
            "ecosystem": "node",
            "manager": "npm",
            "git_repo": root,
            "label": "test",
        }
        fields.update(overrides)
        return Workspace(**fields)

    return _factory


@pytest.fixture(autouse=True)
def _isolate_ambient_config() -> Iterator[None]:
    """Clear the process-wide config snapshot between tests."""
    from repo_ops_core.config import set_ambient_env

    set_ambient_env(None)
    try:
        yield
    finally:
        set_ambient_env(None)


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the XDG cache (and thus the repo-ops state store) at a tmp dir.

    Tests that care about the store's location still override
    ``XDG_CACHE_HOME`` themselves; this just guarantees no test ever reads
    or flushes into the real ``~/.cache/repo-ops/state.json``. The module's
    in-memory cache is also reset — the path redirect alone would let one
    test's loaded state leak into the next via ``_CACHE``.
    """
    from repo_ops_core import state as state_mod

    state_mod._CACHE = None
    state_mod._DIRTY = False
    state_mod._BEHIND_RUN_SEEN.clear()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture(autouse=True)
def _isolate_repo_ops_env() -> Iterator[None]:
    """Snapshot all ``REPO_OPS_*`` env vars before a test and restore after.

    Restores exactly the prior state: vars present before are put back to
    their prior value, and any ``REPO_OPS_*`` var a test added (and didn't
    clean up) is removed.
    """
    before = {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIX)}
    try:
        yield
    finally:
        after = {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIX)}
        for key in after:
            if key not in before:
                del os.environ[key]
        for key, value in before.items():
            os.environ[key] = value
