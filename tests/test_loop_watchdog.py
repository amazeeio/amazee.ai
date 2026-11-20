import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from uvicorn.config import STARTUP_FAILURE

from app.core.loop_watchdog import LoopWatchdog

REPO_ROOT = Path(__file__).resolve().parents[1]

# Runs the real watchdog in a child process, since a firing watchdog exits the
# process. Timeout 0.5s, heartbeat every 0.1s.
CHILD = textwrap.dedent(
    """
    import asyncio, sys, time
    from app.core.loop_watchdog import LoopWatchdog

    def block_the_loop(seconds):
        time.sleep(seconds)  # a sync call on the loop, like a psycopg2 lock wait

    async def main(mode):
        watchdog = LoopWatchdog(0.5, interval=0.1)
        watchdog.start()
        await asyncio.sleep(0.3)
        if mode == "block":
            block_the_loop(10)
        elif mode == "await":
            await asyncio.sleep(1.5)
        elif mode == "block-after-stop":
            await watchdog.stop()
            block_the_loop(1.5)
            return
        await watchdog.stop()

    asyncio.run(main(sys.argv[1]))
    """
)


def run_child(mode: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-c", CHILD, mode],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=8,  # "block" sleeps 10s; only the watchdog ends it sooner
    )


def test_blocked_loop_dumps_stacks_and_exits_with_code_1():
    result = run_child("block")

    assert result.returncode == 1, result.stderr
    # uvicorn stops the whole server, instead of replacing the worker, on this.
    assert result.returncode != STARTUP_FAILURE
    assert "Timeout (0:00:00.500000)!" in result.stderr
    # The dump names the call that blocked the loop.
    assert "in block_the_loop" in result.stderr


def test_turning_loop_never_fires():
    """Awaiting keeps the loop turning, so the timer never runs out."""
    result = run_child("await")

    assert result.returncode == 0, result.stderr
    assert "Timeout" not in result.stderr


def test_stop_disarms_the_timer():
    """A slow shutdown after stop() must not end in a false dump and exit."""
    result = run_child("block-after-stop")

    assert result.returncode == 0, result.stderr
    assert "Timeout" not in result.stderr


@pytest.mark.asyncio
async def test_heartbeat_rearms_until_stop():
    calls = []
    watchdog = LoopWatchdog(
        0.3,
        interval=0.05,
        arm=lambda timeout: calls.append(("arm", timeout)),
        disarm=lambda: calls.append(("disarm",)),
    )
    watchdog.start()
    await asyncio.sleep(0.2)
    await watchdog.stop()
    await asyncio.sleep(0.2)

    arms = [c for c in calls if c[0] == "arm"]
    assert len(arms) >= 3
    assert all(timeout == 0.3 for _, timeout in arms)
    # Disarmed last, and never re-armed after stop().
    assert calls[-1] == ("disarm",)


def test_stall_timeout_must_exceed_interval():
    with pytest.raises(ValueError):
        LoopWatchdog(1.0, interval=1.0)
