package dev.agentplatform.controlplane.queue;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.contract.ContractValidator;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

/**
 * Behavior every {@link RunQueuePublisher} adapter must have: each published run arrives as one
 * message whose body matches contracts/run-message.schema.json.
 */
abstract class RunQueuePublisherContractTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final ContractValidator contracts = new ContractValidator();

    protected RunQueuePublisher publisher;

    /** A publisher writing to a fresh, empty queue. */
    protected abstract RunQueuePublisher newPublisher();

    /** Bodies currently on that queue. */
    protected abstract List<String> receivedBodies();

    @BeforeEach
    void setUp() {
        publisher = newPublisher();
    }

    @Test
    void publishedRunArrivesAsContractMessage() {
        publisher.publish(new RunMessage("run_abc", "tenant-demo"));

        var bodies = receivedBodies();
        assertThat(bodies).hasSize(1);
        var body = JSON.readTree(bodies.getFirst());
        assertThat(contracts.validate(ContractValidator.RUN_MESSAGE, body)).isEmpty();
        assertThat(body.get("run_id").asString()).isEqualTo("run_abc");
        assertThat(body.get("tenant_id").asString()).isEqualTo("tenant-demo");
        assertThat(body.get("schema_version").asInt()).isEqualTo(1);
    }

    @Test
    void eachPublishIsOneMessage() {
        publisher.publish(new RunMessage("run_1", "tenant-demo"));
        publisher.publish(new RunMessage("run_2", "tenant-demo"));

        assertThat(receivedBodies())
                .extracting(b -> JSON.readTree(b).get("run_id").asString())
                .containsExactlyInAnyOrder("run_1", "run_2");
    }
}
