package dev.agentplatform.controlplane.queue;

/**
 * Port for putting a run on the run queue (ADR 0004). The message body is the JSON in
 * contracts/run-message.schema.json. Every adapter must pass {@code RunQueuePublisherContractTest}.
 */
public interface RunQueuePublisher {

    /** @throws RunQueueException if the message could not be sent */
    void publish(RunMessage message);
}
