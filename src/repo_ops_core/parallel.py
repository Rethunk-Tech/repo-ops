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
    should_stop: Callable[[], bool] | None = None,
    stop_on_result: Callable[[R], bool] | None = None,
) -> list[R]:
    """Run *fn* over *items* with at most *max_in_flight* tasks at once.

    When *should_stop* returns true, no new tasks are submitted. When
    *stop_on_result* returns true for a completed result, pending work is
    cancelled and the pool drains.

    Worker exceptions are logged and skipped (the failed item is omitted from
    the returned list) so one bad workspace does not abort the fleet run.
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
        stop_requested = False
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
            if stop_on_result and stop_on_result(result):
                stop_requested = True
                # Cancel only work not already in this completed batch; keep
                # draining the rest of *done* so multi-complete waits stay safe.
                for pending_fut in list(futures):
                    if pending_fut not in done:
                        pending_fut.cancel()
                        futures.pop(pending_fut, None)
                pending.clear()
        if stop_requested:
            futures.clear()
    return results
