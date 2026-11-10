"""Bounded thread-pool execution for upgrade plan/apply."""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable

_LOG = logging.getLogger(__name__)


def run_bounded_pool[T, R](
    pool: concurrent.futures.Executor,
    items: list[T],
    fn: Callable[[T], R],
    *,
    max_in_flight: int,
) -> list[R]:
    """Run *fn* over *items* with at most *max_in_flight* tasks at once.

    Worker exceptions are logged and skipped (the failed item is omitted from
    the returned list) so one bad workspace does not abort the fleet run.
    """
    results: list[R] = []
    pending: list[T] = list(items)
    futures: dict[concurrent.futures.Future[R], T] = {}
    while pending or futures:
        while pending and len(futures) < max_in_flight:
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
            item = futures.pop(fut)
            try:
                result = fut.result()
            except Exception:
                _LOG.exception(
                    "run_bounded_pool worker failed for item=%r",
                    item,
                )
                continue
            results.append(result)
    return results
