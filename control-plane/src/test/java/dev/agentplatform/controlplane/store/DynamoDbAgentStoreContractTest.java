package dev.agentplatform.controlplane.store;

import java.time.Clock;

/** Runs the contract suite against DynamoDB Local in a throwaway container. Needs Docker. */
class DynamoDbAgentStoreContractTest extends AgentStoreContractTest {

    @Override
    protected AgentStore newStore() {
        return new DynamoDbAgentStore(DynamoDbLocal.client(), DynamoDbLocal.newTable(), Clock.systemUTC());
    }
}
