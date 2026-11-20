package dev.agentplatform.controlplane.queue;

import java.util.Map;
import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.MessageAttributeValue;

/** {@link RunQueuePublisher} on Amazon SQS (ElasticMQ locally); the trace context goes in a message attribute. */
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
            sqs.sendMessage(r -> {
                r.queueUrl(queueUrl).messageBody(message.toJson());
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
