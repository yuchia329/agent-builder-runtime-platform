"""Behavior every RunQueue adapter must have. SQS runs against ElasticMQ in Docker (the same
image as the local stack), started by Testcontainers; skip it with `-m "not docker"`.

Timing: adapters use a 1-second visibility timeout so redelivery can be observed quickly.
"""

import time
import uuid

import pytest

from worker.adapters.queue_memory import InMemoryRunQueue
from worker.adapters.queue_sqs import SqsRunQueue

VISIBILITY = 1
ELASTICMQ_IMAGE = "softwaremill/elasticmq-native:1.7.1"  # keep in step with docker-compose.yml


@pytest.fixture(scope="session")
def sqs_client():
    import boto3
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(ELASTICMQ_IMAGE)
        .with_exposed_ports(9324)
        .waiting_for(LogMessageWaitStrategy("started"))
    )
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9324)}"
        yield boto3.client(
            "sqs",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )


def _memory(_request):
    queue = InMemoryRunQueue(visibility_seconds=VISIBILITY)
    return queue, queue.send


def _sqs(request):
    client = request.getfixturevalue("sqs_client")
    url = client.create_queue(QueueName=f"runs-{uuid.uuid4().hex[:8]}")["QueueUrl"]
    queue = SqsRunQueue(url, visibility_seconds=VISIBILITY, client=client)

    def send(body, trace_parent=None):
        attributes = (
            {"traceparent": {"DataType": "String", "StringValue": trace_parent}}
            if trace_parent
            else {}
        )
        client.send_message(QueueUrl=url, MessageBody=body, MessageAttributes=attributes)

    return queue, send


ADAPTERS = [
    pytest.param(_memory, id="memory"),
    pytest.param(_sqs, id="sqs", marks=pytest.mark.docker),
]


@pytest.fixture(params=ADAPTERS)
def queue(request):
    return request.param(request)


def test_receive_returns_sent_body(queue):
    q, send = queue
    send('{"run": 1}')

    [message] = q.receive(10, wait_seconds=1)

    assert message.body == '{"run": 1}'
    assert message.receive_count == 1
    assert message.receipt


def test_trace_parent_comes_from_the_message_attribute(queue):
    q, send = queue
    trace_parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    send('{"run": "traced"}', trace_parent)
    send('{"run": "plain"}')

    messages = {m.body: m for m in q.receive(10, wait_seconds=1)}

    assert messages['{"run": "traced"}'].trace_parent == trace_parent
    assert messages['{"run": "plain"}'].trace_parent is None


def test_receive_on_empty_queue_returns_nothing_after_wait(queue):
    q, _ = queue

    started = time.monotonic()
    assert q.receive(10, wait_seconds=1) == []
    assert time.monotonic() - started >= 0.9


def test_receive_respects_max_messages(queue):
    q, send = queue
    for i in range(3):
        send(f"m{i}")

    assert len(q.receive(2, wait_seconds=1)) <= 2


def test_message_is_hidden_then_redelivered_with_higher_count(queue):
    q, send = queue
    send("again")
    q.receive(10, wait_seconds=1)

    assert q.receive(10, wait_seconds=0) == []
    time.sleep(VISIBILITY + 0.5)
    [message] = q.receive(10, wait_seconds=1)
    assert message.body == "again"
    assert message.receive_count == 2


def test_deleted_message_is_not_redelivered(queue):
    q, send = queue
    send("done")
    [message] = q.receive(10, wait_seconds=1)

    q.delete(message)

    time.sleep(VISIBILITY + 0.5)
    assert q.receive(10, wait_seconds=0) == []


def test_extend_keeps_message_hidden_past_timeout(queue):
    q, send = queue
    send("long run")
    [message] = q.receive(10, wait_seconds=1)

    q.extend(message, 3)

    time.sleep(VISIBILITY + 0.5)
    assert q.receive(10, wait_seconds=0) == []
