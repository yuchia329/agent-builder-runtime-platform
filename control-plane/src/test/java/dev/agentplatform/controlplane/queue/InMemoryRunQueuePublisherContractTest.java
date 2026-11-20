package dev.agentplatform.controlplane.queue;

import java.util.List;

class InMemoryRunQueuePublisherContractTest extends RunQueuePublisherContractTest {

    private InMemoryRunQueuePublisher inMemory;

    @Override
    protected RunQueuePublisher newPublisher() {
        inMemory = new InMemoryRunQueuePublisher();
        return inMemory;
    }

    @Override
    protected List<Received> received() {
        return inMemory.messages().stream().map(m -> new Received(m.toJson(), m.traceParent())).toList();
    }
}
