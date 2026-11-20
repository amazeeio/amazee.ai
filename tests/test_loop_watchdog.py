import asyncio
import io
import threading
import time

import pytest

from app.core import loop_watchdog
from app.core.loop_watchdog import LoopWatchdog, STALL_EXIT_CODE


@pytest.mark.asyncio
async def test_blocked_loop_triggers_on_stall():
    """A sync call that blocks the loop (like a psycopg2 lock wait) trips it."""
    stalls = []
    fired = threading.Event()

    def on_stall(stalled_for):
        stalls.append(stalled_for)
        fired.set()

    watchdog = LoopWatchdog(0.3, interval=0.05, on_stall=on_stall)
    watchdog.start()
    try:
        time.sleep(0.8)  # blocks the event loop, as a sync DB lock wait does
        assert fired.wait(1), "watchdog did not fire on a blocked loop"
        assert len(stalls) == 1
        assert stalls[0] >= 0.3
    finally:
        await watchdog.stop()


@pytest.mark.asyncio
async def test_turning_loop_never_triggers():
    """Awaiting (the loop keeps turning) is not a stall, however long it lasts."""
    stalls = []
    watchdog = LoopWatchdog(0.3, interval=0.05, on_stall=stalls.append)
    watchdog.start()
    try:
        await asyncio.sleep(0.8)
    finally:
        await watchdog.stop()
    assert stalls == []


@pytest.mark.asyncio
async def test_stop_ends_the_watch_thread():
    watchdog = LoopWatchdog(0.3, interval=0.05, on_stall=lambda _: None)
    watchdog.start()
    await watchdog.stop()
    watchdog._thread.join(1)
    assert not watchdog._thread.is_alive()


def test_stall_timeout_must_exceed_interval():
    with pytest.raises(ValueError):
        LoopWatchdog(1.0, interval=1.0)


def test_exit_stalled_worker_dumps_stacks_and_hard_exits(monkeypatch):
    calls = []
    monkeypatch.setattr(
        loop_watchdog.faulthandler,
        "dump_traceback",
        lambda **kw: calls.append(("dump", kw.get("all_threads"))),
    )
    monkeypatch.setattr(
        loop_watchdog.os, "_exit", lambda code: calls.append(("exit", code))
    )

    loop_watchdog.exit_stalled_worker(61.0)

    assert calls == [("dump", True), ("exit", STALL_EXIT_CODE)]
    # uvicorn stops the whole server, instead of replacing the worker, on 3.
    assert STALL_EXIT_CODE != 3


def test_exit_stalled_worker_exits_when_the_stack_dump_fails(monkeypatch):
    """A stderr with no fileno() makes the dump raise; the worker must still exit."""
    exits = []

    def broken_dump(**_):
        raise io.UnsupportedOperation("fileno")

    monkeypatch.setattr(loop_watchdog.faulthandler, "dump_traceback", broken_dump)
    monkeypatch.setattr(loop_watchdog.os, "_exit", exits.append)

    # The real os._exit never returns, so the error never surfaces; the stub does.
    with pytest.raises(io.UnsupportedOperation):
        loop_watchdog.exit_stalled_worker(61.0)

    assert exits == [STALL_EXIT_CODE]
