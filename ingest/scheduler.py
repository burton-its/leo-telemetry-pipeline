"""Runs a job over and over on a fixed interval."""

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)


def run_every(
    interval_seconds: float,
    job: Callable[[], object],
    fatal: tuple[type[BaseException], ...] = (),
    expected: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_runs: int | None = None,
) -> None:
    """Call job() every interval_seconds until stopped.

    A failed run is logged and retried on the next interval, so one bad run
    doesn't stop collection. Exceptions listed in `fatal` won't fix themselves
    by waiting (like a bad token), so those stop the loop instead. Exceptions
    listed in `expected` (like the network being down) are normal enough that
    they get a one-line log instead of a full traceback.
    """
    runs = 0
    while True:
        started = clock()
        try:
            job()
        except fatal:
            raise
        except expected as err:
            logger.error("Run failed (%s: %s), trying again in %.0f minutes",
                         type(err).__name__, err, interval_seconds / 60)
        except Exception:
            logger.exception("Run failed, trying again in %.0f minutes", interval_seconds / 60)
        runs += 1
        if max_runs is not None and runs >= max_runs:
            return
        sleep(max(0.0, interval_seconds - (clock() - started)))
