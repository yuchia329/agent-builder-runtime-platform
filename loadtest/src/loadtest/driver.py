"""Starts N runs at a set rate and polls them until each is final (async, one event loop)."""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from loadtest.api import Api, ApiError
from loadtest.stats import FINAL

# A run waiting for an approval will not finish by itself: the driver stops polling it.
STOP_POLLING = FINAL | {"WAITING_APPROVAL"}


def open_runs(outcome: "Outcome | None", expected: int) -> int:
    """Runs still to finish: all `expected` while they are being started (`outcome` None), then
    those not final. Chaos uses it, so it starts with the runs, not after the last is sent."""
    if outcome is None:
        return expected
    return sum(1 for r in outcome.runs.values() if r.get("status") not in STOP_POLLING)


def schedule(count: int, rate_per_s: float) -> list[float]:
    """Seconds after the start at which each run is started: evenly spaced at `rate_per_s`."""
    if count < 0:
        raise ValueError("count must be 0 or more")
    if rate_per_s <= 0:
        raise ValueError("rate must be above 0")
    return [i / rate_per_s for i in range(count)]


@dataclass
class Outcome:
    runs: dict[str, dict] = field(default_factory=dict)  # run id -> last GET /runs/{id}
    start_errors: list[str] = field(default_factory=list)
    timed_out: bool = False


async def start_runs(
    api: Api,
    agent: str,
    run_input: dict,
    count: int,
    rate_per_s: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Outcome:
    """Each POST /runs leaves at its scheduled time whether or not earlier ones have answered,
    so a slow API does not lower the rate."""
    outcome = Outcome()
    began = clock()
    pending = []
    for offset in schedule(count, rate_per_s):
        delay = began + offset - clock()
        if delay > 0:
            await sleep(delay)
        pending.append(asyncio.create_task(api.start_run(agent, run_input)))
    for result in await asyncio.gather(*pending, return_exceptions=True):
        if isinstance(result, BaseException):
            outcome.start_errors.append(_describe(result))
        else:
            outcome.runs[result["run_id"]] = result
    return outcome


async def poll_until_final(
    api: Api,
    outcome: Outcome,
    *,
    poll_s: float = 10.0,
    timeout_s: float = 1800.0,
    concurrency: int = 20,
    on_round: Callable[[int, int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Outcome:
    """Rounds of GET /runs/{id} for every run not yet final (at most `concurrency` at once),
    `poll_s` apart, until all are final or `timeout_s` passes. A failed GET keeps the run's last
    known state for the next round (an API error or a network error alike)."""
    limit = asyncio.Semaphore(concurrency)
    began = clock()

    async def refresh(run_id: str) -> None:
        async with limit:
            with contextlib.suppress(ApiError, httpx.HTTPError):
                outcome.runs[run_id] = await api.get_run(run_id)

    while True:
        open_ids = [i for i, r in outcome.runs.items() if r.get("status") not in STOP_POLLING]
        if on_round is not None:
            on_round(len(outcome.runs) - len(open_ids), len(outcome.runs))
        if not open_ids:
            return outcome
        if clock() - began > timeout_s:
            outcome.timed_out = True
            return outcome
        await sleep(poll_s)
        await asyncio.gather(*(refresh(i) for i in open_ids))


def _describe(error: BaseException) -> str:
    if isinstance(error, ApiError):
        return f"HTTP {error.status}"
    return type(error).__name__
