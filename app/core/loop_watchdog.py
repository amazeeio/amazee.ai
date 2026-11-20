"""Recycle a uvicorn worker whose event loop has stopped turning.

The handlers are async, but the database driver is synchronous. A query that
waits on a row lock blocks its whole worker, including any request on that
worker that would have committed the lock. Postgres can't see that as a
deadlock, so nothing ends it, and once every worker is blocked the whole API
stops. Neither health check notices: uvicorn's supervisor pings each
worker from a helper thread, not from the loop, and Lagoon's probes only open
the TCP port. A frozen worker stays "healthy" until someone restarts the pod.

The watchdog has two parts: a heartbeat task on the loop, and a daemon thread
that checks it. When the loop misses its heartbeat for ``stall_timeout``
seconds, the thread dumps every thread's stack to stderr, which shows what
blocked the loop, and hard-exits the worker. Uvicorn's supervisor sees the dead
worker and starts a fresh one. The exit drops the worker's DB connections, so
Postgres rolls back their transactions and releases the locks they held.

It uses ``os._exit``, not ``sys.exit`` or a signal. The loop is blocked, so
nothing that needs the loop can run, and a graceful shutdown would wait on the
very requests that are stuck.
"""

import asyncio
import contextlib
import faulthandler
import logging
import os
import sys
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# EX_SOFTWARE. Must not be uvicorn's STARTUP_FAILURE (3): the supervisor stops
# the whole server on that code instead of replacing the worker.
STALL_EXIT_CODE = 70


def exit_stalled_worker(stalled_for: float) -> None:
    # The diagnostics are best effort. If stderr is closed or has no fileno(),
    # the dump raises, and without the finally the frozen worker would live on.
    try:
        logger.critical(
            "Event loop stalled for %.0fs; dumping stacks and exiting worker %s so "
            "uvicorn starts a fresh one",
            stalled_for,
            os.getpid(),
        )
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.flush()
    finally:
        os._exit(STALL_EXIT_CODE)


class LoopWatchdog:
    def __init__(
        self,
        stall_timeout: float,
        *,
        interval: float = 1.0,
        on_stall: Callable[[float], None] = exit_stalled_worker,
    ):
        if stall_timeout <= interval:
            raise ValueError("stall_timeout must be longer than interval")
        self._stall_timeout = stall_timeout
        self._interval = interval
        self._on_stall = on_stall
        self._last_beat = time.monotonic()
        self._stop = threading.Event()
        self._task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None

    async def _beat(self) -> None:
        while True:
            self._last_beat = time.monotonic()
            await asyncio.sleep(self._interval)

    def _watch(self) -> None:
        while not self._stop.wait(self._interval):
            stalled_for = time.monotonic() - self._last_beat
            if stalled_for >= self._stall_timeout:
                self._on_stall(stalled_for)
                return

    def start(self) -> None:
        """Start watching the running loop. Call from inside it."""
        self._last_beat = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._beat())
        self._thread = threading.Thread(
            target=self._watch, name="event-loop-watchdog", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
