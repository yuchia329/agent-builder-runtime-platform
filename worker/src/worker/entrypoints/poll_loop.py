"""Fargate entrypoint: long-poll the run queue and call handle_run for each message (ADR 0005).

- Up to `concurrency` runs at a time, each on its own thread; only free slots are asked for.
- While a run is in progress, a heartbeat keeps its message hidden from other workers.
- The message is deleted once handle_run returns, whatever status the run ended in. It is kept
  (so SQS delivers it again, and after N tries moves it to the DLQ) when the body is invalid, the
  run is not found, or handle_run raised (control plane unreachable, a bug).
- stop() ends polling; drain() waits for runs in progress.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from worker.core.errors import QueueError, RunNotFound
from worker.core.handle_run import Deps, handle_run
from worker.core.run_message import InvalidRunMessage, parse_run_message
from worker.core.types import RunStatus
from worker.ports.queue import QueueMessage, RunQueue

log = logging.getLogger(__name__)

Handler = Callable[[str, Deps], RunStatus]


class PollLoop:
    def __init__(
        self,
        queue: RunQueue,
        deps: Deps,
        *,
        concurrency: int = 4,
        visibility_seconds: float = 60,
        wait_seconds: int = 20,
        error_pause_seconds: float = 5,
        handler: Handler = handle_run,
    ) -> None:
        self._queue = queue
        self._deps = deps
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

        with _Heartbeat(self._queue, message, self._visibility):
            try:
                status = self._handler(run.run_id, self._deps)
            except RunNotFound:
                log.warning("run %s not found (receive %d)", run.run_id, message.receive_count)
                return
            except Exception:  # noqa: BLE001 - keep the message; it is retried
                log.exception("run %s failed to process; message kept for retry", run.run_id)
                return

        log.info("run %s ended %s", run.run_id, status)
        try:
            self._queue.delete(message)
        except QueueError:
            # The run is already final; a redelivery is skipped by handle_run's status check.
            log.exception("could not delete message for run %s", run.run_id)


class _Heartbeat:
    """Extends the message's visibility every third of the timeout while the block runs."""

    def __init__(self, queue: RunQueue, message: QueueMessage, visibility_seconds: float) -> None:
        self._queue = queue
        self._message = message
        self._visibility = visibility_seconds
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
