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
from collections.abc import Iterator
from pathlib import Path

import pytest

_ENV_PREFIX = "REPO_OPS_"


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the XDG cache (and thus the repo-ops state store) at a tmp dir.

    Tests that care about the store's location still override
    ``XDG_CACHE_HOME`` themselves; this just guarantees no test ever reads
    or flushes into the real ``~/.cache/repo-ops/state.json``.
    """
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
