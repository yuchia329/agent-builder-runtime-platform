package dev.agentplatform.controlplane.store;

import java.time.Clock;

class InMemoryAgentStoreContractTest extends AgentStoreContractTest {

    @Override
    protected AgentStore newStore() {
        return new InMemoryAgentStore(Clock.systemUTC());
    }
}
