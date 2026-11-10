"""Tests for bounded thread-pool execution."""

from __future__ import annotations

import concurrent.futures

from repo_ops_core.parallel import run_bounded_pool


def test_run_bounded_pool_raising_task_does_not_abort_pool(caplog):
    """A raising worker must not abort the pool; other results are returned."""
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
