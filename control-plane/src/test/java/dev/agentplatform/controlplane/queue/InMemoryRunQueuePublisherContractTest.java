package dev.agentplatform.controlplane.queue;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class InMemoryRunQueuePublisherContractTest extends RunQueuePublisherContractTest {

    private InMemoryRunQueuePublisher inMemory;

    @Override
    protected RunQueuePublisher newPublisher() {
        inMemory = new InMemoryRunQueuePublisher();
        return inMemory;
    }

    @Override
    protected List<Received> received() {
        return inMemory.messages().stream().map(m -> new Received(m.toJson(), m.traceParent(), m.tenantId())).toList();
    }

    @Test
    void delayIsRecorded() {
        inMemory.publish(new RunMessage("run_later", "tenant-demo"), 30);

        assertThat(inMemory.sent()).extracting(InMemoryRunQueuePublisher.Sent::delaySeconds).containsExactly(30);
    }
}
