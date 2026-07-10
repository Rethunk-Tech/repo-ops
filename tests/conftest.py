"""Shared pytest fixtures for the top-level test suite.

Isolates ``REPO_OPS_*`` environment variables between tests. Several code
paths (notably ``repo_ops_upgrade.run``'s ``--latest`` handling) mutate
``os.environ`` directly rather than going through a fixture, and existing
tests that exercise those paths don't know to restore every var that might
get set. Left unchecked, one test's ``REPO_OPS_*`` mutation leaks into the
next test in the same pytest process (order-dependent failures that don't
reproduce in isolation).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_ENV_PREFIX = "REPO_OPS_"


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
