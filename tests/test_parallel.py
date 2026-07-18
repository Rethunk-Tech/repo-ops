"""Tests for bounded thread-pool execution."""

from __future__ import annotations

import concurrent.futures
from unittest.mock import patch

from repo_ops_core.parallel import run_bounded_pool


def test_run_bounded_pool_raising_task_does_not_abort_pool(caplog):
    """#51 — a raising worker must not abort the pool; other results are returned."""
    import logging

    def worker(x: int) -> int:
        if x == 1:
            raise ValueError("boom")
        return x * 10

    with (
        caplog.at_level(logging.ERROR, logger="repo_ops_core.parallel"),
        concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool,
    ):
        results = run_bounded_pool(pool, [0, 1, 2], worker, max_in_flight=2)

    assert 0 in results
    assert 20 in results
    # The raising task must not appear in results (no fabricated value)
    assert len(results) == 2
    assert any("worker failed" in rec.message for rec in caplog.records)


def test_run_bounded_pool_stop_on_result_multiple_done_no_keyerror():
    """stop_on_result must not KeyError when wait() returns several done futures."""
    wait_calls = 0

    def wait_return_all_done(fs, **kwargs):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            return set(fs), set()
        return set(), set()

    with (
        patch.object(concurrent.futures, "wait", side_effect=wait_return_all_done),
        concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool,
    ):
        results = run_bounded_pool(
            pool,
            [0, 1],
            lambda x: x,
            max_in_flight=2,
            stop_on_result=lambda r: r == 0,
        )

    assert 0 in results
    assert len(results) == 2
