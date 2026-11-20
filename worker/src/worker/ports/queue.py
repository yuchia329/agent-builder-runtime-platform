from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QueueMessage:
    """One received message. `receipt` identifies this delivery; it changes on redelivery."""

    body: str
    receipt: str
    receive_count: int
    # The run's trace (W3C traceparent) from the message attribute `traceparent`, if any
    # (ADR 0022); not part of the body.
    trace_parent: str | None = None


class RunQueue(Protocol):
    """The run queue, receive side (contracts/run-message.schema.json is the body).

    Delivery is at least once. A received message is hidden from other receivers for the
    visibility timeout, then delivered again unless deleted. After N receives it moves to a
    dead-letter queue. Adapters: SQS, in-memory. Contract: tests/contract. Errors: QueueError.
    """

    def receive(self, max_messages: int, wait_seconds: int) -> list[QueueMessage]:
        """Up to max_messages (at most 10), waiting up to wait_seconds for the first one."""
        ...

    def delete(self, message: QueueMessage) -> None: ...

    def extend(self, message: QueueMessage, seconds: int) -> None:
        """Keeps the message hidden for `seconds` from now (heartbeat)."""
        ...
