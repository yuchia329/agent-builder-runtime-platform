package dev.agentplatform.controlplane.queue;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sqs.SqsClient;

/** Runs the contract suite against ElasticMQ (local SQS) in a throwaway container. Needs Docker. */
@Testcontainers
class SqsRunQueuePublisherContractTest extends RunQueuePublisherContractTest {

    @Container
    static final GenericContainer<?> ELASTICMQ = new GenericContainer<>("softwaremill/elasticmq-native:1.7.1")
            .withExposedPorts(9324);

    private static SqsClient sqs;
    private String queueUrl;

    @BeforeAll
    static void connect() {
        sqs = SqsClient.builder()
                .endpointOverride(URI.create("http://%s:%d".formatted(ELASTICMQ.getHost(), ELASTICMQ.getMappedPort(9324))))
                .region(Region.US_EAST_1)
                .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create("local", "local")))
                .build();
    }

    @AfterAll
    static void close() {
        sqs.close();
    }

    @Override
    protected RunQueuePublisher newPublisher() {
        queueUrl = sqs.createQueue(r -> r.queueName("runs-" + UUID.randomUUID())).queueUrl();
        return new SqsRunQueuePublisher(sqs, queueUrl);
    }

    @Override
    protected List<Received> received() {
        var received = new ArrayList<Received>();
        while (true) {
            var messages = sqs.receiveMessage(r -> r.queueUrl(queueUrl).maxNumberOfMessages(10).waitTimeSeconds(1)
                    .messageAttributeNames("All").messageSystemAttributeNamesWithStrings("MessageGroupId")).messages();
            if (messages.isEmpty()) {
                return received;
            }
            messages.forEach(m -> {
                var trace = m.messageAttributes().get(RunMessage.TRACE_ATTRIBUTE);
                received.add(new Received(m.body(), trace == null ? null : trace.stringValue(),
                        m.attributesAsStrings().get("MessageGroupId")));
            });
        }
    }

    @Test
    void delayedMessageArrivesLater() throws InterruptedException {
        publisher.publish(new RunMessage("run_later", "tenant-demo"), 2);

        assertThat(received()).isEmpty();
        Thread.sleep(2_000);
        assertThat(received()).extracting(Received::body).hasSize(1);
    }
}
