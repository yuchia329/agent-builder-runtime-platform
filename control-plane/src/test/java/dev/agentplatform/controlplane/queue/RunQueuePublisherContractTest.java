package dev.agentplatform.controlplane.queue;

import static org.assertj.core.api.Assertions.assertThat;

import dev.agentplatform.controlplane.contract.ContractValidator;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

/**
 * Behavior every {@link RunQueuePublisher} adapter must have: each published run arrives as one
 * message whose body matches contracts/run-message.schema.json, with the run's trace context in
 * the {@code traceparent} message attribute when there is one (ADR 0022).
 */
abstract class RunQueuePublisherContractTest {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private final ContractValidator contracts = new ContractValidator();

    protected RunQueuePublisher publisher;

    /** A publisher writing to a fresh, empty queue. */
    protected abstract RunQueuePublisher newPublisher();

    /** A message on the queue: its body and its {@code traceparent} attribute (null if absent). */
    protected record Received(String body, String traceParent) {}

    /** Messages currently on that queue (read once: a real queue hands each out once). */
    protected abstract List<Received> received();

    private List<String> receivedBodies() {
        return received().stream().map(Received::body).toList();
    }

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
    void traceContextTravelsAsAttributeNotInBody() {
        var traceParent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
        publisher.publish(new RunMessage("run_traced", "tenant-demo", traceParent));
        publisher.publish(new RunMessage("run_plain", "tenant-demo"));

        var byRun = received().stream().collect(java.util.stream.Collectors.toMap(
                m -> JSON.readTree(m.body()).get("run_id").asString(), m -> m));
        assertThat(byRun.get("run_traced").traceParent()).isEqualTo(traceParent);
        assertThat(byRun.get("run_plain").traceParent()).isNull();
        var body = JSON.readTree(byRun.get("run_traced").body());
        assertThat(contracts.validate(ContractValidator.RUN_MESSAGE, body)).isEmpty();
        assertThat(body.has("traceparent")).isFalse();
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
