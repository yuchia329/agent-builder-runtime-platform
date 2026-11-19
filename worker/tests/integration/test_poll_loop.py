"""The poll loop end to end with in-memory adapters, and once over HTTP against the fake
control plane server: message in, run finished, message deleted (or kept, when it should be)."""

import json
import threading
import time

import pytest
from contracts import example_run
from fake_control_plane_server import http_client

from worker.adapters.clock_system import SystemClock
from worker.adapters.control_plane_memory import InMemoryControlPlane
from worker.adapters.model_stub import StubModel, demo_turns
from worker.adapters.queue_memory import InMemoryRunQueue
from worker.adapters.run_state_memory import InMemoryRunStateStore
from worker.adapters.tools_fake import issue_triage_tools
from worker.core.errors import ControlPlaneError, StateError
from worker.core.handle_run import Deps
from worker.core.types import RunStatus
from worker.entrypoints.poll_loop import PollLoop

RUN = example_run()
RUN_ID = RUN["run_id"]


def _body(run_id=RUN_ID):
    return json.dumps({"schema_version": 1, "run_id": run_id, "tenant_id": RUN["tenant_id"]})


def _deps(control_plane=None):
    if control_plane is None:
        control_plane = InMemoryControlPlane()
        control_plane.add_run(example_run())
    return Deps(control_plane, StubModel(demo_turns()), issue_triage_tools(), SystemClock())


def _state():
    return InMemoryRunStateStore(SystemClock())


def _run_once(loop):
    started = loop.poll_once()
    loop.drain()
    return started


def test_message_runs_to_succeeded_and_is_deleted():
    queue = InMemoryRunQueue()
    queue.send(_body())
    deps = _deps()

    assert _run_once(PollLoop(queue, deps, _state(), wait_seconds=0)) == 1

    run = deps.control_plane.run_json(RUN_ID)
    assert run["status"] == "SUCCEEDED"
    assert run["output"].startswith("Labeled #42")
    assert [name for name, _ in deps.tools.calls] == ["github.search_issues", "github.add_label"]
    assert queue.pending() == 0


def test_over_http_against_the_contract(fake_control_plane_server):
    fake_control_plane_server.state.add_run(example_run())
    queue = InMemoryRunQueue()
    queue.send(_body())

    _run_once(
        PollLoop(queue, _deps(http_client(fake_control_plane_server)), _state(), wait_seconds=0)
    )

    assert fake_control_plane_server.state.run_json(RUN_ID)["status"] == "SUCCEEDED"
    assert queue.pending() == 0


def test_duplicate_delivery_of_finished_run_is_deleted_without_rerunning():
    queue = InMemoryRunQueue()
    deps = _deps()
    queue.send(_body())
    _run_once(PollLoop(queue, deps, _state(), wait_seconds=0))
    tool_calls = len(deps.tools.calls)

    queue.send(_body())
    _run_once(PollLoop(queue, deps, _state(), wait_seconds=0))

    assert len(deps.tools.calls) == tool_calls
    assert queue.pending() == 0


@pytest.mark.parametrize("body", ["not json", '{"schema_version": 1, "runId": "x"}'])
def test_invalid_message_is_kept_for_the_dead_letter_queue(body):
    queue = InMemoryRunQueue()
    queue.send(body)

    _run_once(PollLoop(queue, _deps(), _state(), wait_seconds=0))

    assert queue.pending() == 1


def test_unknown_run_is_kept_for_retry():
    queue = InMemoryRunQueue()
    queue.send(_body("run_missing"))

    _run_once(PollLoop(queue, _deps(), _state(), wait_seconds=0))

    assert queue.pending() == 1


def test_control_plane_outage_keeps_message_for_retry():
    def unreachable(run_id, deps, attempt):
        raise ControlPlaneError("connection refused")

    queue = InMemoryRunQueue()
    queue.send(_body())

    _run_once(PollLoop(queue, _deps(), _state(), wait_seconds=0, handler=unreachable))

    assert queue.pending() == 1


def test_heartbeat_extends_visibility_while_run_is_in_progress():
    release = threading.Event()

    def slow(run_id, deps, attempt):
        release.wait(5)
        return RunStatus.SUCCEEDED

    queue = InMemoryRunQueue(visibility_seconds=0.3)
    queue.send(_body())
    loop = PollLoop(queue, _deps(), _state(), visibility_seconds=0.3, wait_seconds=0, handler=slow)

    loop.poll_once()
    time.sleep(0.8)  # well past the original timeout
    assert queue.receive(1, wait_seconds=0) == []  # still hidden: not handed to another worker
    release.set()
    loop.drain()

    assert len(queue.extends) >= 2
    assert queue.pending() == 0


def test_asks_only_for_free_slots():
    release = threading.Event()
    started = []

    def blocking(run_id, deps, attempt):
        started.append(run_id)
        release.wait(5)
        return RunStatus.SUCCEEDED

    queue = InMemoryRunQueue()
    for i in range(3):
        queue.send(_body(f"run_{i}"))
    loop = PollLoop(queue, _deps(), _state(), concurrency=2, wait_seconds=0, handler=blocking)

    assert loop.poll_once() == 2
    poller = threading.Thread(target=loop.poll_once)
    poller.start()
    time.sleep(0.2)
    assert poller.is_alive()  # waiting for a free slot, not receiving
    release.set()
    poller.join(5)
    loop.drain()

    assert sorted(started) == ["run_0", "run_1", "run_2"]
    assert queue.pending() == 0


def test_stop_ends_run_forever():
    loop = PollLoop(InMemoryRunQueue(), _deps(), _state(), wait_seconds=0)
    thread = threading.Thread(target=loop.run_forever)
    thread.start()

    loop.stop()
    thread.join(2)

    assert not thread.is_alive()


# Lease (ADR 0011)


def _held(run_id=RUN_ID, seconds=30):
    state = _state()
    assert state.take_lease(run_id, "other-worker/1", seconds)
    return state


def test_run_leased_by_another_worker_is_not_run_and_message_is_hidden():
    queue = InMemoryRunQueue()
    queue.send(_body())
    deps = _deps()

    _run_once(PollLoop(queue, deps, _held(), wait_seconds=0))

    assert deps.tools.calls == []
    assert deps.control_plane.run_json(RUN_ID)["status"] == "QUEUED"
    assert queue.pending() == 1  # kept: the holder may still crash
    assert queue.extends  # hidden for one more lease length
    assert queue.receive(1, wait_seconds=0) == []


def test_two_workers_given_the_same_run_execute_it_once():
    release = threading.Event()
    calls = []

    def slow(run_id, deps, attempt):
        calls.append(run_id)
        release.wait(5)
        return RunStatus.SUCCEEDED

    queue = InMemoryRunQueue()
    queue.send(_body())
    queue.send(_body())  # duplicate delivery
    state = _state()
    first = PollLoop(
        queue, _deps(), state, worker_id="a", concurrency=1, wait_seconds=0, handler=slow
    )
    second = PollLoop(
        queue, _deps(), state, worker_id="b", concurrency=1, wait_seconds=0, handler=slow
    )

    first.poll_once()
    second.poll_once()
    second.drain()
    release.set()
    first.drain()

    assert calls == [RUN_ID]


def test_same_process_receiving_a_run_twice_runs_it_once():
    release = threading.Event()
    calls = []

    def slow(run_id, deps, attempt):
        calls.append(run_id)
        release.wait(5)
        return RunStatus.SUCCEEDED

    queue = InMemoryRunQueue()
    queue.send(_body())
    queue.send(_body())
    loop = PollLoop(queue, _deps(), _state(), concurrency=2, wait_seconds=0, handler=slow)

    loop.poll_once()
    time.sleep(0.2)
    release.set()
    loop.drain()

    assert calls == [RUN_ID]


def test_lease_is_released_after_the_run_so_a_retry_can_take_it_at_once():
    state = _state()
    queue = InMemoryRunQueue()
    queue.send(_body())

    def unreachable(run_id, deps, attempt):
        raise ControlPlaneError("connection refused")

    _run_once(PollLoop(queue, _deps(), state, wait_seconds=0, handler=unreachable))

    assert queue.pending() == 1
    assert state.take_lease(RUN_ID, "next-worker/1", 30) is not None


def test_heartbeat_renews_the_lease_past_its_first_expiry():
    release = threading.Event()

    def slow(run_id, deps, attempt):
        release.wait(5)
        return RunStatus.SUCCEEDED

    state = _state()
    queue = InMemoryRunQueue(visibility_seconds=0.3)
    queue.send(_body())
    loop = PollLoop(queue, _deps(), state, visibility_seconds=0.3, wait_seconds=0, handler=slow)

    loop.poll_once()
    time.sleep(0.8)  # well past the first lease expiry
    assert state.take_lease(RUN_ID, "other-worker/1", 30) is None
    release.set()
    loop.drain()


def test_lost_lease_stops_the_run_before_its_next_step_and_keeps_the_message():
    class StealingTools:
        """Fake tools; the first call lets another worker take the run (as if this worker
        stalled past its lease expiry)."""

        def __init__(self, inner, state):
            self._inner, self._state, self.calls = inner, state, []

        def describe(self, names, context=None):
            return self._inner.describe(names)

        def call(self, name, arguments, context=None):
            self.calls.append(name)
            self._state.release_lease(RUN_ID, self._state.holder)
            self._state.take_lease(RUN_ID, "thief/1", 30)
            time.sleep(0.4)  # one heartbeat notices
            return self._inner.call(name, arguments)

    class RecordingState(InMemoryRunStateStore):
        holder = None

        def take_lease(self, run_id, owner, seconds):
            lease = super().take_lease(run_id, owner, seconds)
            if lease and self.holder is None:
                self.holder = owner
            return lease

    state = RecordingState(SystemClock())
    control_plane = InMemoryControlPlane()
    control_plane.add_run(example_run())
    tools = StealingTools(issue_triage_tools(), state)
    deps = Deps(control_plane, StubModel(demo_turns()), tools, SystemClock())
    queue = InMemoryRunQueue(visibility_seconds=0.3)
    queue.send(_body())

    _run_once(PollLoop(queue, deps, state, visibility_seconds=0.3, wait_seconds=0))

    assert tools.calls == ["github.search_issues"]  # stopped before add_label
    assert control_plane.run_json(RUN_ID)["status"] == "RUNNING"  # left to the new holder
    assert queue.pending() == 1
    assert state.take_lease(RUN_ID, "someone/1", 30) is None  # the thief keeps its lease


def test_state_store_outage_keeps_the_message():
    class Down(InMemoryRunStateStore):
        def take_lease(self, run_id, owner, seconds):
            raise StateError("DynamoDB unreachable")

    queue = InMemoryRunQueue()
    queue.send(_body())
    deps = _deps()

    _run_once(PollLoop(queue, deps, Down(SystemClock()), wait_seconds=0))

    assert deps.tools.calls == []
    assert queue.pending() == 1
