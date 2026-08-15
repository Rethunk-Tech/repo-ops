"""Yarn extraction concurrency cap (prevents /tmp ENOSPC from parallel yarn up).

Each yarn check/guard run copies the workspace to a temp dir and lets Yarn Berry
extract its cache there; running one per ``--jobs`` worker can exhaust /tmp. The
``_yarn_extraction_slot`` semaphore bounds concurrent extractions independently
of the worker pool (env ``REPO_OPS_YARN_MAX_CONCURRENCY``).
"""

from __future__ import annotations

import threading
import time

import pytest
from repo_ops_js_yarn import yarn


@pytest.fixture(autouse=True)
def _reset_extraction_semaphore():
    # The slot semaphore is a lazily-initialized module global; reset around
    # each test so env-driven sizing takes effect deterministically.
    yarn._extraction_sem = None
    yield
    yarn._extraction_sem = None


def test_resolve_default_is_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPO_OPS_YARN_MAX_CONCURRENCY", raising=False)
    assert yarn._resolve_yarn_max_concurrency() == 4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", 1), ("2", 2), ("0", 1), ("-3", 1), ("", 4), ("abc", 4)],
)
def test_resolve_env_override(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("REPO_OPS_YARN_MAX_CONCURRENCY", raw)
    assert yarn._resolve_yarn_max_concurrency() == expected


def test_slot_caps_concurrent_extractions(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the cap at 2, no more than 2 slots are ever held simultaneously."""
    monkeypatch.setenv("REPO_OPS_YARN_MAX_CONCURRENCY", "2")
    active = 0
    peak = 0
    lock = threading.Lock()
    n = 6
    start = threading.Barrier(n)

    def worker() -> None:
        nonlocal active, peak
        start.wait()  # release all workers together to force contention
        with yarn._yarn_extraction_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak >= 1
    assert peak <= 2, f"extraction slots exceeded the cap: peak={peak}"
