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
    protected List<String> receivedBodies() {
        return inMemory.bodies();
    }
}
