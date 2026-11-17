"""Fargate entrypoint: long-poll the run queue and call handle_run for each message (ADR 0005).

- Up to `concurrency` runs at a time, each on its own thread; only free slots are asked for.
- Before a run starts, the worker takes the run's lease in the run-state store (ADR 0011), as
  a new owner per attempt (`<worker id>/<random>`), so even one process never runs a run twice.
  If another worker holds a live lease, the message is kept and hidden for one more lease length.
- While a run is in progress, a heartbeat keeps its message hidden from other workers and renews
  the lease. If the lease is lost, the run stops before its next step (handle_run raises
  LeaseLost) and the message is kept. The lease is released when handle_run returns or raises.
- The message is deleted once handle_run returns, whatever status the run ended in. It is kept
  (so SQS delivers it again, and after N tries moves it to the DLQ) when the body is invalid, the
  run is not found, or handle_run raised (control plane unreachable, a bug).
- stop() ends polling; drain() waits for runs in progress.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from worker.core.errors import LeaseLost, QueueError, RunNotFound, StateError
from worker.core.handle_run import Attempt, Deps, handle_run
from worker.core.run_message import InvalidRunMessage, parse_run_message
from worker.core.types import RunStatus
from worker.ports.queue import QueueMessage, RunQueue
from worker.ports.run_state import RunStateStore

log = logging.getLogger(__name__)

Handler = Callable[[str, Deps, Attempt], RunStatus]  # like handle_run


class PollLoop:
    def __init__(
        self,
        queue: RunQueue,
        deps: Deps,
        state: RunStateStore,
        *,
        worker_id: str | None = None,
        concurrency: int = 4,
        visibility_seconds: float = 60,
        wait_seconds: int = 20,
        error_pause_seconds: float = 5,
        handler: Handler = handle_run,
    ) -> None:
        self._queue = queue
        self._deps = deps
        self._state = state
        self._worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self._concurrency = concurrency
        self._visibility = visibility_seconds
        self._wait = wait_seconds
        self._error_pause = error_pause_seconds
        self._handler = handler
        self._stop = threading.Event()
        self._slots = threading.Condition()
        self._in_flight = 0
        self._executor = ThreadPoolExecutor(concurrency, thread_name_prefix="run")

    def run_forever(self) -> None:
        log.info("polling for runs (concurrency %d)", self._concurrency)
        while not self._stop.is_set():
            self.poll_once()
        log.info("stopped polling")

    def stop(self) -> None:
        self._stop.set()
        with self._slots:
            self._slots.notify_all()

    def drain(self) -> None:
        """Waits for every run in progress to finish."""
        self._executor.shutdown(wait=True)

    def poll_once(self) -> int:
        """One receive call for the free slots. Returns how many runs were started."""
        with self._slots:
            while self._in_flight >= self._concurrency and not self._stop.is_set():
                self._slots.wait()
            free = self._concurrency - self._in_flight
        if self._stop.is_set():
            return 0

        try:
            messages = self._queue.receive(free, self._wait)
        except QueueError:
            log.exception("receive failed; pausing %ss", self._error_pause)
            self._stop.wait(self._error_pause)
            return 0

        with self._slots:
            self._in_flight += len(messages)
        for message in messages:
            self._executor.submit(self._process_and_release, message)
        return len(messages)

    def _process_and_release(self, message: QueueMessage) -> None:
        try:
            self._process(message)
        except Exception:  # noqa: BLE001 - never let a worker thread die silently
            log.exception("unexpected error processing a message")
        finally:
            with self._slots:
                self._in_flight -= 1
                self._slots.notify_all()

    def _process(self, message: QueueMessage) -> None:
        try:
            run = parse_run_message(message.body)
        except InvalidRunMessage as e:
            # Kept: after N receives SQS moves it to the DLQ for inspection.
            log.error("invalid run message (receive %d): %s", message.receive_count, e)
            return

        owner = f"{self._worker_id}/{uuid.uuid4().hex[:8]}"
        try:
            lease = self._state.take_lease(run.run_id, owner, self._visibility)
        except StateError:
            log.exception("run %s: could not take the lease; message kept for retry", run.run_id)
            return
        if lease is None:
            log.info("run %s is leased by another worker; message hidden for now", run.run_id)
            self._hide(message)
            return

        beat = _Heartbeat(self._queue, message, self._visibility, self._state, run.run_id, owner)
        try:
            with beat:
                try:
                    status = self._handler(run.run_id, self._deps, Attempt(owner, beat.lost.is_set))
                except RunNotFound:
                    log.warning("run %s not found (receive %d)", run.run_id, message.receive_count)
                    return
                except LeaseLost:
                    log.warning("run %s: lease lost; message kept", run.run_id)
                    return
                except Exception:  # noqa: BLE001 - keep the message; it is retried
                    log.exception("run %s failed to process; message kept for retry", run.run_id)
                    return
        finally:
            try:
                self._state.release_lease(run.run_id, owner)
            except StateError:
                log.exception("run %s: could not release the lease; it expires", run.run_id)

        log.info("run %s ended %s", run.run_id, status)
        try:
            self._queue.delete(message)
        except QueueError:
            # The run is already final; a redelivery is skipped by handle_run's status check.
            log.exception("could not delete message for run %s", run.run_id)

    def _hide(self, message: QueueMessage) -> None:
        """Keeps a message for a leased run out of sight for one lease length; by then the
        holder has renewed (hide again) or finished (the next delivery is skipped and deleted)."""
        try:
            self._queue.extend(message, self._visibility)
        except QueueError:
            log.exception("could not hide the message; it may come back early")


class _Heartbeat:
    """Every third of the visibility timeout while the block runs: extends the message's
    visibility and renews the run's lease. `lost` is set once the lease is gone."""

    def __init__(
        self,
        queue: RunQueue,
        message: QueueMessage,
        visibility_seconds: float,
        state: RunStateStore,
        run_id: str,
        owner: str,
    ) -> None:
        self._queue = queue
        self._message = message
        self._visibility = visibility_seconds
        self._state = state
        self._run_id = run_id
        self._owner = owner
        self.lost = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="heartbeat", daemon=True)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._done.set()
        self._thread.join()

    def _beat(self) -> None:
        while not self._done.wait(self._visibility / 3):
            try:
                self._queue.extend(self._message, self._visibility)
            except QueueError:
                log.exception("heartbeat failed; the message may be delivered again")
            if self.lost.is_set():
                continue
            try:
                self._state.renew_lease(self._run_id, self._owner, self._visibility)
            except LeaseLost:
                log.warning("run %s: lease lost; the run stops before its next step", self._run_id)
                self.lost.set()
            except StateError:
                log.exception("run %s: lease renewal failed; retrying next beat", self._run_id)
