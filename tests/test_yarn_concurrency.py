"""Yarn extraction concurrency cap (prevents /tmp ENOSPC from parallel yarn up).

Each yarn check/guard run copies the workspace to a temp dir and lets Yarn Berry
extract its cache there; running one per ``--jobs`` worker can exhaust /tmp. The
``_yarn_extraction_slot`` semaphore bounds concurrent extractions independently
of the worker pool.
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


def test_slot_caps_concurrent_extractions(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the cap at 2, no more than 2 slots are ever held simultaneously."""
    monkeypatch.setattr(yarn, "_DEFAULT_YARN_MAX_CONCURRENCY", 2)
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
