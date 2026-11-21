package dev.agentplatform.controlplane.queue;

/**
 * Port for putting a run on the run queue (ADR 0004). The message body is the JSON in
 * contracts/run-message.schema.json; its message group is the run's tenant, so a fair queue serves
 * a quiet tenant ahead of another's backlog (ADR 0026). Every adapter must pass
 * {@code RunQueuePublisherContractTest}.
 */
public interface RunQueuePublisher {

    /** Delivered at once. @throws RunQueueException if the message could not be sent */
    default void publish(RunMessage message) {
        publish(message, 0);
    }

    /**
     * Delivered after {@code delaySeconds} (0 to {@value #MAX_DELAY_SECONDS}, SQS's limit).
     *
     * @throws RunQueueException if the message could not be sent
     */
    void publish(RunMessage message, int delaySeconds);

    int MAX_DELAY_SECONDS = 900;
}
