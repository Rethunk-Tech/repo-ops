"""Bounded thread-pool execution for upgrade plan/apply."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def run_bounded_pool(
    pool: concurrent.futures.Executor,
    items: list[T],
    fn: Callable[[T], R],
    *,
    max_in_flight: int,
    should_stop: Callable[[], bool] | None = None,
    stop_on_result: Callable[[R], bool] | None = None,
) -> list[R]:
    """Run *fn* over *items* with at most *max_in_flight* tasks at once.

    When *should_stop* returns true, no new tasks are submitted. When
    *stop_on_result* returns true for a completed result, pending work is
    cancelled and the pool drains.
    """
    results: list[R] = []
    pending: list[T] = list(items)
    futures: dict[concurrent.futures.Future[R], T] = {}
    while pending or futures:
        while (
            pending
            and len(futures) < max_in_flight
            and not (should_stop and should_stop())
        ):
            item = pending.pop(0)
            fut = pool.submit(fn, item)
            futures[fut] = item
        if not futures:
            break
        done, _ = concurrent.futures.wait(
            futures.keys(),
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        for fut in done:
            if fut in futures:
                futures.pop(fut)
            try:
                result = fut.result()
            except Exception:
                continue
            results.append(result)
            if stop_on_result and stop_on_result(result):
                for pending_fut in list(futures):
                    if pending_fut not in done:
                        pending_fut.cancel()
                futures.clear()
                pending.clear()
    return results
