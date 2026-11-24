"""Recycle a uvicorn worker whose event loop has stopped turning.

The handlers are async, but the database driver is synchronous. A query that
waits on a row lock blocks its whole worker, including any request on that
worker that would have committed the lock. Postgres can't see that as a
deadlock, so nothing ends it, and once every worker is blocked the whole API
stops. Neither health check notices: uvicorn's supervisor pings each
worker from a helper thread, not from the loop, and Lagoon's probes only open
the TCP port. A frozen worker stays "healthy" until someone restarts the pod.

The watchdog is faulthandler's own timer. A heartbeat task on the loop re-arms
``faulthandler.dump_traceback_later(stall_timeout, exit=True)`` every
``interval`` seconds. While the loop turns, the timer never runs out. Once the
loop stops, it does: faulthandler writes every thread's stack to stderr (it
starts with ``Timeout (0:01:00)!`` and shows what blocked the loop) and exits
the worker with code 1. Uvicorn's supervisor sees the dead worker and starts a
fresh one; it stops the whole server only on STARTUP_FAILURE (3). The exit drops
the worker's DB connections, so Postgres rolls back their transactions and
releases the locks they held.

The dump and the exit run in faulthandler's C thread. They take neither the GIL
nor the logging lock, so a frozen thread that holds either cannot stop the exit.
The output goes to file descriptor 2 directly, not through ``sys.stderr``.

faulthandler has one such timer per process. Nothing else in the app uses it.
"""

import asyncio
import contextlib
import faulthandler
from typing import Callable, Optional

STDERR_FD = 2


def _arm(timeout: float) -> None:
    faulthandler.dump_traceback_later(timeout, exit=True, file=STDERR_FD)


class LoopWatchdog:
    def __init__(
        self,
        stall_timeout: float,
        *,
        interval: float = 1.0,
        arm: Callable[[float], None] = _arm,
        disarm: Callable[[], None] = faulthandler.cancel_dump_traceback_later,
    ):
        if stall_timeout <= interval:
            raise ValueError("stall_timeout must be longer than interval")
        self._stall_timeout = stall_timeout
        self._interval = interval
        self._arm = arm
        self._disarm = disarm
        self._task: Optional[asyncio.Task] = None

    async def _beat(self) -> None:
        while True:
            # Each call cancels the previous timer and starts a new one.
            self._arm(self._stall_timeout)
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        """Start watching the running loop. Call from inside it."""
        # Arm now: the first beat only runs once the loop gets to the task.
        self._arm(self._stall_timeout)
        self._task = asyncio.get_running_loop().create_task(self._beat())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        # After the task is gone, so it cannot re-arm the timer.
        self._disarm()
