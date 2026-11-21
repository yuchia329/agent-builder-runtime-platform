package dev.agentplatform.controlplane.queue;

import java.util.Map;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.MessageAttributeValue;

/**
 * {@link RunQueuePublisher} on Amazon SQS (ElasticMQ locally). The trace context goes in a message
 * attribute; the tenant is the {@code MessageGroupId}, which makes a standard queue a fair queue.
 */
public class SqsRunQueuePublisher implements RunQueuePublisher {

    private final SqsClient sqs;
    private final String queueUrl;

    public SqsRunQueuePublisher(SqsClient sqs, String queueUrl) {
        this.sqs = sqs;
        this.queueUrl = queueUrl;
    }

    @Override
    public void publish(RunMessage message, int delaySeconds) {
        if (delaySeconds < 0 || delaySeconds > MAX_DELAY_SECONDS) {
            throw new IllegalArgumentException("delaySeconds must be 0 to " + MAX_DELAY_SECONDS);
        }
        try {
            sqs.sendMessage(r -> {
                r.queueUrl(queueUrl).messageBody(message.toJson()).messageGroupId(message.tenantId());
                if (delaySeconds > 0) {
                    r.delaySeconds(delaySeconds);
                }
                if (message.traceParent() != null) {
                    r.messageAttributes(Map.of(RunMessage.TRACE_ATTRIBUTE, MessageAttributeValue.builder()
                            .dataType("String").stringValue(message.traceParent()).build()));
                }
            });
        } catch (SdkException e) {
            throw new RunQueueException("Could not enqueue run " + message.runId(), e);
        }
    }
}
