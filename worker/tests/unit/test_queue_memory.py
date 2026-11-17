"""In-memory queue behavior not covered by the contract suite: the dead-letter move."""

import time

import pytest

from worker.adapters.queue_memory import InMemoryRunQueue
from worker.core.errors import QueueError


def test_moves_to_dead_letter_after_max_receives():
    q = InMemoryRunQueue(visibility_seconds=0.05, max_receives=2)
    q.send("poison")

    for _ in range(2):
        assert len(q.receive(1, wait_seconds=1)) == 1
        time.sleep(0.1)

    assert q.receive(1, wait_seconds=0) == []
    assert q.dead == ["poison"]
    assert q.pending() == 0


def test_delete_after_timeout_raises():
    q = InMemoryRunQueue(visibility_seconds=0.05)
    q.send("slow")
    [message] = q.receive(1, wait_seconds=1)
    time.sleep(0.1)

    with pytest.raises(QueueError):
        q.delete(message)
