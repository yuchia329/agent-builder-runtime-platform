"""In-memory RunQueue with SQS semantics: visibility timeout, receive count, dead-letter list."""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass

from worker.core.errors import QueueError
from worker.ports.queue import QueueMessage


@dataclass
class _Entry:
    body: str
    receive_count: int = 0
    visible_at: float = 0.0
    receipt: str | None = None


class InMemoryRunQueue:
    def __init__(self, visibility_seconds: float = 60, max_receives: int = 5) -> None:
        self._visibility = visibility_seconds
        self._max_receives = max_receives
        self._entries: list[_Entry] = []
        self._receipts = itertools.count(1)
        self._cond = threading.Condition()
        self.dead: list[str] = []  # bodies moved to the dead-letter list
        self.extends: list[tuple[str, float]] = []  # (receipt, seconds), for heartbeat tests

    def send(self, body: str) -> None:
        with self._cond:
            self._entries.append(_Entry(body))
            self._cond.notify_all()

    def pending(self) -> int:
        """Messages not yet deleted (visible or in flight)."""
        with self._cond:
            return len(self._entries)

    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        deadline = time.monotonic() + wait_seconds
        with self._cond:
            while True:
                now = time.monotonic()
                taken = self._take_visible(now, min(max_messages, 10))
                if taken or now >= deadline:
                    return taken
                # Wake on send, or poll for timeouts expiring.
                self._cond.wait(timeout=min(deadline - now, 0.05))

    def _take_visible(self, now: float, limit: int) -> list[QueueMessage]:
        taken = []
        for entry in list(self._entries):
            if len(taken) == limit:
                break
            if entry.visible_at > now:
                continue
            if entry.receive_count >= self._max_receives:
                self._entries.remove(entry)
                self.dead.append(entry.body)
                continue
            entry.receive_count += 1
            entry.visible_at = now + self._visibility
            entry.receipt = f"r{next(self._receipts)}"
            taken.append(QueueMessage(entry.body, entry.receipt, entry.receive_count))
        return taken

    def _held(self, receipt: str) -> _Entry | None:
        now = time.monotonic()
        for entry in self._entries:
            if entry.receipt == receipt and entry.visible_at > now:
                return entry
        return None

    def delete(self, message: QueueMessage) -> None:
        with self._cond:
            entry = self._held(message.receipt)
            if entry is None:
                raise QueueError("message is no longer held by this receiver")
            self._entries.remove(entry)

    def extend(self, message: QueueMessage, seconds: int) -> None:
        with self._cond:
            entry = self._held(message.receipt)
            if entry is None:
                raise QueueError("message is no longer held by this receiver")
            entry.visible_at = time.monotonic() + seconds
            self.extends.append((message.receipt, seconds))
