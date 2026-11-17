package dev.agentplatform.controlplane.queue;

import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.sqs.SqsClient;

/** {@link RunQueuePublisher} on Amazon SQS (ElasticMQ locally). */
public class SqsRunQueuePublisher implements RunQueuePublisher {

    private final SqsClient sqs;
    private final String queueUrl;

    public SqsRunQueuePublisher(SqsClient sqs, String queueUrl) {
        this.sqs = sqs;
        this.queueUrl = queueUrl;
    }

    @Override
    public void publish(RunMessage message) {
        try {
            sqs.sendMessage(r -> r.queueUrl(queueUrl).messageBody(message.toJson()));
        } catch (SdkException e) {
            throw new RunQueueException("Could not enqueue run " + message.runId(), e);
        }
    }
}
